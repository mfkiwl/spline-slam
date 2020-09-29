import numpy as np
import math
import time
from scipy.optimize import least_squares

class SplineLocalization:
    def __init__(self, spline_map, **kwargs): 
        # Parameters
        min_angle = kwargs['min_angle'] if 'min_angle' in kwargs else 0.
        max_angle = kwargs['max_angle'] if 'max_angle' in kwargs else 2.*np.pi 
        angle_increment = kwargs['angle_increment'] if 'angle_increment' in kwargs else 1.*np.pi/180.
        range_min = kwargs['range_min'] if 'range_min' in kwargs else 0.12
        range_max = kwargs['range_max'] if 'range_max' in kwargs else 3.5
        logodd_min_free = kwargs['logodd_min_free'] if 'logodd_min_free' in kwargs else -100
        logodd_max_occupied = kwargs['logodd_max_occupied'] if 'logodd_max_occupied' in kwargs else 100
        det_Hinv_threshold = kwargs['det_Hinv_threshold'] if 'det_Hinv_threshold' in kwargs else 1e-3
        nb_iteration_max = kwargs['nb_iteration_max'] if 'nb_iteration_max' in kwargs else 10
        alpha = kwargs['alpha'] if 'alpha' in kwargs else 2

        # LogOdd Map parameters
        self.map = spline_map
        self.logodd_min_free = logodd_min_free
        self.logodd_max_occupied = logodd_max_occupied

        # Sensor scan parameters
        self.min_angle = min_angle
        self.max_angle = max_angle 
        self.angle_increment = angle_increment
        self.range_min = range_min
        self.range_max = range_max
        self.angles = np.arange(min_angle, max_angle, angle_increment )                

        # Localization parameters
        self.nb_iteration_max = nb_iteration_max        
        self.det_Hinv_threshold = det_Hinv_threshold
        self.pose = np.zeros(3)
        self.alpha = alpha
        self.sensor_subsampling_factor = 1 
        
        # Time
        self.time = np.zeros(3)  

    """Removes spurious (out of range) measurements
        Input: ranges np.array<float>
    """ 
    def remove_spurious_measurements(self, ranges):
        # Finding indices of the valid ranges
        ind_occ = np.logical_and(ranges >= self.range_min, ranges < self.range_max)
        return ranges[ind_occ], self.angles[ind_occ]

    """ Transforms ranges measurements to (x,y) coordinates (local frame) """
    def range_to_coordinate(self, ranges, angles):
        angles = np.array([np.cos(angles), np.sin(angles)]) 
        return ranges * angles 

    """ Transform an [2xn] array of (x,y) coordinates to the global frame
        Input: pose np.array<float(3,1)> describes (x,y,theta)'
    """
    def local_to_global_frame(self, pose, local):
        c, s = np.cos(pose[2]), np.sin(pose[2])
        R = np.array([[c, -s],[s, c]])
        return np.matmul(R, local) + pose[0:2].reshape(2,1)
    
    """ Estimate pose (core function) """
    def compute_pose(self, pose_estimate, pts_occ_local, ftol=1e-5):
        res = least_squares(self.scipy_cost_function, 
                            pose_estimate,
                            jac = self.scipy_jacobian, 
                            verbose = 0, 
                            method='dogbox',
                            loss='cauchy',
                            ftol = ftol,
                            f_scale = 3./2,
                            args=pts_occ_local )
        return res.x, res.cost

    def scipy_jacobian(self, pose, pts_occ_local_x, pts_occ_local_y):
        pts_occ_local = np.vstack([pts_occ_local_x, pts_occ_local_y])
        # Transforming occupied points to global frame
        pts_occ = self.local_to_global_frame(pose, pts_occ_local)
        # Spline tensor
        c_index_occ = self.map.compute_sparse_tensor_index(pts_occ)
        _, dBx_occ, dBy_occ = self.map.compute_tensor_spline(pts_occ, ORDER= 0x02)                
        # Rotation matrix
        cos, sin = np.cos(pose[2]), np.sin(pose[2])
        R = np.array([[-sin, -cos],[cos, -sin]])           
        # compute H and b  
        ds_occ = np.zeros([2, len(pts_occ_local_x)])
        ds_occ[0,:]=np.sum((self.map.ctrl_pts[c_index_occ]/ self.logodd_max_occupied) *dBx_occ, axis=1) 
        ds_occ[1,:]=np.sum((self.map.ctrl_pts[c_index_occ]/ self.logodd_max_occupied) *dBy_occ, axis=1) 
        dpt_occ_local = R@pts_occ_local
    
        # Jacobian
        h_occ = np.zeros([3, len(pts_occ_local_x)])
        h_occ[0,:] = ds_occ[0,:]
        h_occ[1,:] = ds_occ[1,:]
        h_occ[2,:] = np.sum(dpt_occ_local*ds_occ,axis=0)

        return h_occ.T

    def scipy_cost_function(self, pose, pts_occ_local_x, pts_occ_local_y):
        # computing alignment error
        pts_occ_local = np.vstack([pts_occ_local_x, pts_occ_local_y])
        pts_occ = self.local_to_global_frame(pose, pts_occ_local)
        c_index_occ = self.map.compute_sparse_tensor_index(pts_occ)
        B_occ, _, _ = self.map.compute_tensor_spline(pts_occ, ORDER=0x01)        
        s_occ = np.sum(self.map.ctrl_pts[c_index_occ]*B_occ, axis=1)        
        r = (1 - s_occ/self.logodd_max_occupied)
        return r



    """"Occupancy grid mapping routine to update map using range measurements"""
    def update_localization(self, ranges, pose_estimative=None, unreliable_odometry=True):
        map = None
        if pose_estimative is None:
            pose_estimative = np.copy(self.pose)
        # Removing spurious measurements
        tic = time.time()
        ranges_occ, angles = self.remove_spurious_measurements(ranges)
        self.time[0] += time.time() - tic
        # Converting range measurements to metric coordinates
        tic = time.time()
        pts_occ_local = self.range_to_coordinate(ranges_occ, angles)
        self.pts_occ_local = pts_occ_local
        #pts_free_local = self.detect_free_space(ranges)
        self.time[1] += time.time() - tic
        # Localization
        tic = time.time()      
        best_cost_estimate = np.inf
        if unreliable_odometry:
            candidate = [0, np.pi/4., -np.pi/4., np.pi/2., -np.pi/2, -1.5*np.pi, -1.5*np.pi]
        else:
            candidate = [0]
        for theta in candidate:
            pose_estimate_candidate, cost_estimate = self.compute_pose(np.array(pose_estimative) + np.array([0,0,theta]), pts_occ_local, ftol=1e-2)
            if cost_estimate < best_cost_estimate:
                best_cost_estimate = cost_estimate
                best_pose_estimate = pose_estimate_candidate
        pose_self, cost_self= self.compute_pose(self.pose, pts_occ_local, ftol = 1e-2)        

        if best_cost_estimate < cost_self:
            self.pose, _ = self.compute_pose(best_pose_estimate, pts_occ_local)
        else:
            self.pose, _ = self.compute_pose(pose_self, pts_occ_local)

        #self.pose, _ = self.compute_pose(pose_estimative, pts_occ_local)
        self.time[2] += time.time() - tic