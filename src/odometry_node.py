#!/usr/bin/python
import tf
import rospy
import numpy as np
from sensor_msgs.msg import JointState
from nav_msgs.msg import Odometry
from visualization_msgs.msg import Marker
from geometry_msgs.msg import Point
from std_msgs.msg import ColorRGBA 
from untils.EKF_3DOFDifferentialDriveInputDisplacement import *
import math

wheelBase   = 0.235
wheelRadius = 0.035
odom_freq   = 0.1
odom_window = 100000.0
Qk = np.diag(np.array([0.01 ** 2, 0.001 ** 2, np.deg2rad(0.1) ** 2]))  # covariance of simulated displacement noise

class encoderReading:
    def __init__(self):
        self.position   = 0.0
        self.velocity   = 0.0
        self.stamp      = None

def normalize_angle(angle):
    while angle >= math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += 2 * math.pi
    return angle

class EKF:
    def __init__(self, odom_topic) -> None:

        # PUBLISHERS
        # Publisher for sending Odometry
        # self.odom_pub = rospy.Publisher(cmd_vel_topic, Twist, queue_size=1)
        # Publisher for visualizing the path to with rviz
        # self.marker_pub = rospy.Publisher('~path_marker', Marker, queue_size=1)
        self.point_marker_pub   = rospy.Publisher('~point_marker', Marker, queue_size=1)

        # self.tf_world_base_footprint_pub = rospy.Publisher('~point_marker', tf, queue_size=1)
        self.odom_pub           = rospy.Publisher('/odom', Odometry, queue_size=1)
        # SUBSCRIBERS
        self.odom_sub               = rospy.Subscriber(odom_topic, JointState, self.get_odom) 
        self.ground_truth_sub       = rospy.Subscriber('/turtlebot/odom_ground_truth', Odometry, self.get_ground_truth) 

        self.synchronized_velocity  = [0.0, 0.0]
        self.synchronized_stamp     = None
        self.inited_displacement    = False
        self.left_reading           = encoderReading()
        self.right_reading          = encoderReading()

        self.current_pose           = None
        self.xk                     = None
        self.Pk                     = None
        
        self.ekf_filter = EKF_3DOFDifferentialDriveInputDisplacement(self.xk, self.Pk)

        # Init computing displacement
        while True:
            if self.synchronized_stamp is not None and self.current_pose is not None:
                self.reset_displacement(0)
                break
        
        

        # TIMERS
        # Timer for displacement reset
        rospy.Timer(rospy.Duration(odom_window), self.reset_displacement)

        # rospy.Timer(rospy.Duration(0.01), self.run_EKF)

    def run_EKF(self):
        # Get input to prediction step
        uk              = self.uk
        Qk              = np.diag(np.array([0.01 ** 2, 0.001 ** 2, np.deg2rad(0.1) ** 2]))
        # Prediction step
        xk_bar, Pk_bar  = self.ekf_filter.Prediction(uk, Qk, self.xk, self.Pk)

        # # Get measurement, Heading of the robot
        # zk, Rk, Hk, Vk  = self.GetMeasurements()
        # Update step
        xk, Pk          = self.ekf_filter.Update(self.zk, self.Rk, xk_bar, Pk_bar, self.Hk, self.Vk)

        self.xk = xk
        self.Pk = Pk

        self.xk[2,0] = normalize_angle(self.xk[2,0])
        # self.displacement += self.uk
        self.publish_point(self.xk[0:2])
        self.odom_path_pub()
    
    # Odometry callback: Gets current robot pose and stores it into self.current_pose
    def get_ground_truth(self, odom):
        _, _, yaw = tf.transformations.euler_from_quaternion([odom.pose.pose.orientation.x, 
                                                            odom.pose.pose.orientation.y,
                                                            odom.pose.pose.orientation.z,
                                                            odom.pose.pose.orientation.w])
        self.current_pose = np.array([odom.pose.pose.position.x, odom.pose.pose.position.y, yaw])
    # if self.xk is not None:
        ns              = 3
        self.zk         = np.array([yaw]).reshape(1,1)
        self.Hk         = np.zeros((1,ns))
        self.Hk[0,2]    = 1
        self.Rk         = np.diag([np.deg2rad(1)**2])
        # Compute V matrix
        self.Vk         = np.diag([1.])
        self.ekf_filter.gotNewHeadingData()

    def get_odom(self, odom):
        # Check if encoder data is for the left wheel
        if 'turtlebot/wheel_left_joint' in odom.name:
            self.left_reading.position  = odom.position[0]
            self.left_reading.velocity  = odom.velocity[0]
            self.left_reading.stamp     = odom.header.stamp
        # Check if encoder data is for the right wheel
        elif 'turtlebot/wheel_right_joint' in odom.name:
            self.right_reading.position = odom.position[0]
            self.right_reading.velocity = odom.velocity[0]
            self.right_reading.stamp    = odom.header.stamp
        
        # Synchronize encoder data if readings for both wheels are available
        if self.left_reading.stamp is not None and self.right_reading.stamp is not None:
            a = rospy.Time.now() 
            next_synchronized_stamp     = a.secs + a.nsecs/1e9 #0.5 * ((self.left_reading.stamp.secs + self.right_reading.stamp.secs) + (self.left_reading.stamp.nsecs + self.right_reading.stamp.nsecs)/1e9)  
            # Compute displacement
            if self.synchronized_stamp is not None and self.inited_displacement is True:
                delta_t = next_synchronized_stamp - self.synchronized_stamp
                self.uk = self.compute_displacement(delta_t)
                self.ekf_filter.gotNewEncoderData()
                self.run_EKF()

            self.synchronized_stamp     = next_synchronized_stamp
            # Synchronize encoder readings here
            # For demonstration, let's assume the readings are already synchronized
            self.synchronized_velocity  = [self.left_reading.velocity, self.right_reading.velocity]
            # Publish synchronized data or use it in your control algorithm

            # Reset readings for next iteration
            self.left_reading.stamp     = None
            self.right_reading.stamp    = None

            return True
        
        return False
    
    def compute_displacement(self, delta_t):
        d_L = self.synchronized_velocity[0] * wheelRadius * delta_t
        d_R = self.synchronized_velocity[1] * wheelRadius * delta_t
        # Compute displacement of the center point of robot between k-1 and k
        d       = (d_L + d_R) / 2.
        # Compute rotated angle of robot around the center point between k-1 and k
        delta_theta_k   = np.arctan2(-d_R + d_L, wheelBase)

        # Compute xk from xk_1 and the travel distance and rotated angle. Got the equations from chapter 1.4.1: Odometry 
        uk              = np.array([[d],
                                    [0],
                                    [delta_theta_k]])
        
        self.v = d / delta_t
        self.w = delta_theta_k / delta_t
        
        return uk

    def reset_displacement(self, event):
        self.xk           = self.current_pose.reshape(3,1)
        self.Pk           = np.zeros((3, 3))

        self.displacement = np.array([[0.0],
                                      [0.0],
                                      [0.0]])
        self.inited_displacement = True

    # Publish markers
    def publish_point(self,p):
        if p is not None:
            m = Marker()
            m.header.frame_id = 'world_ned'
            m.header.stamp = rospy.Time.now()
            m.ns = 'point'
            m.id = 0
            m.type = Marker.SPHERE
            m.action = Marker.ADD
            m.pose.position.x = p[0]
            m.pose.position.y = p[1]
            m.pose.position.z = 0.0
            m.pose.orientation.x = 0
            m.pose.orientation.y = 0
            m.pose.orientation.z = 0
            m.pose.orientation.w = 1
            m.scale.x = 0.05
            m.scale.y = 0.05
            m.scale.z = 0.05
            m.color.a = 1.0
            m.color.r = 0.0
            m.color.g = 1.0
            m.color.b = 0.0
            self.point_marker_pub.publish(m)

    def odom_path_pub(self):
        # Transform theta from euler to quaternion
        quaternion = tf.transformations.quaternion_from_euler(0, 0, float((self.xk[2, 0])))  # Convert euler angles to quaternion

        # Publish predicted odom
        odom = Odometry()
        odom.header.stamp = rospy.Time.now()
        odom.header.frame_id = "world_ned"
        odom.child_frame_id = "turtlebot/kobuki/base_footprint"


        odom.pose.pose.position.x = self.xk[0]
        odom.pose.pose.position.y = self.xk[1]

        odom.pose.pose.orientation.x = quaternion[0]
        odom.pose.pose.orientation.y = quaternion[1]
        odom.pose.pose.orientation.z = quaternion[2]
        odom.pose.pose.orientation.w = quaternion[3]

        odom.pose.covariance = list(np.array([[self.Pk[0, 0], self.Pk[0, 1], 0, 0, 0, self.Pk[0, 2]],
                                [self.Pk[1, 0], self.Pk[1,1], 0, 0, 0, self.Pk[1, 2]],
                                [0, 0, 0, 0, 0, 0],
                                [0, 0, 0, 0, 0, 0],
                                [0, 0, 0, 0, 0, 0],
                                [self.Pk[2, 0], self.Pk[2, 1], 0, 0, 0, self.Pk[2, 2]]]).flatten())

        odom.twist.twist.linear.x = self.v
        odom.twist.twist.angular.z = self.w

        self.odom_pub.publish(odom)

        tf.TransformBroadcaster().sendTransform((float(self.xk[0, 0]), float(self.xk[1, 0]), 0.0), quaternion, rospy.Time.now(), odom.child_frame_id, odom.header.frame_id)
        
    def spin(self):
        pass

if __name__ == '__main__':
    rospy.init_node('odom_publisher')
    node = EKF('/turtlebot/joint_states')	
    
    rate = rospy.Rate(odom_freq)
    while not rospy.is_shutdown():
        node.spin()
        rate.sleep()