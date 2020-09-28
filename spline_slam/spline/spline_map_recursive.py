import numpy as np
import math
import time
import random
import scipy.sparse.linalg

class SplineMap:
    def __init__(self, **kwargs):
        # Parameters
        knot_space = kwargs['knot_space'] if 'knot_space' in kwargs else .05
        map_size = kwargs['map_size'] if 'map_size' in kwargs else np.array([10.,10.]) 
        min_angle = kwargs['min_angle'] if 'min_angle' in kwargs else 0.
        max_angle = kwargs['max_angle'] if 'max_angle' in kwargs else 2.*np.pi - 1.*np.pi/180.
        angle_increment = kwargs['angle_increment'] if 'angle_increment' in kwargs else 1.*np.pi/180.
        range_min = kwargs['range_min'] if 'range_min' in kwargs else 0.12
        range_max = kwargs['range_max'] if 'range_max' in kwargs else 3.6
        logodd_occupied = kwargs['logodd_occupied'] if 'logodd_occupied' in kwargs else .9
        logodd_free = kwargs['logodd_free'] if 'logodd_free' in kwargs else .3
        logodd_min_free = kwargs['logodd_min_free'] if 'logodd_min_free' in kwargs else -100
        logodd_max_occupied = kwargs['logodd_max_occupied'] if 'logodd_max_occupied' in kwargs else 100
        max_nb_rays = kwargs['max_nb_rays'] if 'max_nb_rays' in kwargs else 360


        # Spline-map parameters
        # @TODO grid_size has to be greater than (2d x 2d)
        self.degree = 3
        self.knot_space = knot_space
        self.grid_size = np.ceil(map_size/knot_space+self.degree).astype(int).reshape([2,1]) 
        self.grid_center = np.ceil((self.grid_size-self.degree)/2).reshape(2,1) + self.degree - 1  
        self.ctrl_pts =  3*(logodd_max_occupied+logodd_min_free)*np.ones((self.grid_size[0,0], self.grid_size[1,0]) ).flatten()

        # Map parameters
        self.map_increment = range_max    
        self.map_lower_limits = (self.degree - self.grid_center)*self.knot_space
        self.map_upper_limits = (self.grid_size-self.grid_center+1)*self.knot_space          

        # LogOdd Map parameters
        self.logodd_occupied = logodd_occupied
        self.logodd_free = logodd_free
        self.logodd_min_free = logodd_min_free
        self.logodd_max_occupied = logodd_max_occupied
        self.free_detection_spacing = 2*knot_space 
        self.free_ranges = np.arange(max(knot_space, range_min), range_max, self.free_detection_spacing)       
        
        # Sensor scan parameters
        self.min_angle = min_angle
        self.max_angle = max_angle 
        self.angle_increment = angle_increment
        self.range_min = range_min
        self.range_max = range_max
        self.angles = np.arange(min_angle, max_angle, angle_increment )
        self.sensor_subsampling_factor = max(divmod(len(self.angles),max_nb_rays)[0],1)

        # Storing ranges for speed up 
        self.free_ranges_matrix = np.tile(self.free_ranges.reshape(-1,1), (1,len(self.angles)))
        self.ray_matrix_x = self.free_ranges.reshape(-1,1) * np.cos(self.angles)
        self.ray_matrix_y = self.free_ranges.reshape(-1,1) * np.sin(self.angles)    


        self.time = np.zeros(5)           
    """Removes spurious (out of range) measurements
        Input: ranges np.array<float>
    """ 
    def remove_spurious_measurements(self, ranges):
        # Finding indices of the valid ranges
        ind_occ = np.logical_and(ranges >= self.range_min, ranges < self.range_max)
        return ranges[ind_occ], self.angles[ind_occ]

    """ Transforms ranges measurements to (x,y) coordinates (local frame) """
    def range_to_coordinate(self, ranges, angles):
        direction = np.array([np.cos(angles), np.sin(angles)]) 
        return  ranges * direction
    
    """ Transform an [2xn] array of (x,y) coordinates to the global frame
        Input: pose np.array<float(3,1)> describes (x,y,theta)'
    """
    def local_to_global_frame(self, pose, local):
        c, s = np.cos(pose[2]), np.sin(pose[2])
        R = np.array([[c, -s],[s, c]])
        return np.matmul(R, local) + pose[0:2].reshape(2,1) 

    """ Detect free space """
    def detect_free_space(self, ranges):      
        ranges_free = ranges 
        init = int(np.random.rand()*self.sensor_subsampling_factor)
        index_free =  np.where((ranges_free >= self.range_min) & (ranges  <= self.range_max))[0][init::self.sensor_subsampling_factor]
        index_free_matrix = self.free_ranges_matrix[:,index_free] <  \
                            (ranges_free[index_free]).reshape([1,-1])
        index_free_matrix[:,0:-1] = np.logical_and(index_free_matrix[:,0:-1], index_free_matrix[:,1:]) 
        index_free_matrix[:,1:] = np.logical_and(index_free_matrix[:,1:], index_free_matrix[:,0:-1])  

        pts_free = np.vstack([self.ray_matrix_x[:, index_free][index_free_matrix],
                            self.ray_matrix_y[:, index_free][index_free_matrix]]) 

        if pts_free.size == 0:
            pts_free = np.zeros([2,1])

        return pts_free 

    """ Compute spline coefficient index associated to the sparse representation """
    def compute_sparse_spline_index(self, tau, origin):
        mu    = -(np.ceil(-tau/self.knot_space).astype(int)) + origin
        c = np.zeros([len(tau),(self.degree+1)],dtype='int')
        for i in range(0, self.degree+1):
            c[:,i] = mu-self.degree+i
        return c

    """ Compute spline tensor coefficient index associated to the sparse representation """
    def compute_sparse_tensor_index(self, pts):
        # Compute spline along each axis
        cx = self.compute_sparse_spline_index(pts[0,:], self.grid_center[0,0])
        cy = self.compute_sparse_spline_index(pts[1,:], self.grid_center[1,0])

        # Kronecker product for index
        c = np.zeros([cx.shape[0],(self.degree+1)**2],dtype='int')
        for i in range(0, self.degree+1):
            for j in range(0, self.degree+1):
                c[:,i*(self.degree+1)+j] = cy[:,i]*(self.grid_size[0,0])+cx[:,j]
        return c

    """"Compute spline coefficients - 1D function """
    def compute_spline(self, tau, origin, ORDER=0):
        tau_bar = (tau/self.knot_space + origin) % 1 
        tau_3 = tau_bar + 3
        tau_2 = tau_bar + 2        
        tau_1 = tau_bar + 1
        tau_0 = tau_bar
        
        b = np.zeros([len(tau),self.degree+1])
        b[:,0] = 1/(6)*(-tau_3**3 + 12*tau_3**2 - 48*tau_3 + 64) 
        b[:,1] = 1/(6)*(3*tau_2**3 - 24*tau_2**2 + 60*tau_2 - 44)
        b[:,2] = 1/(6)*(-3*tau_1**3 + 12*tau_1**2 - 12*tau_1 + 4)
        b[:,3] = 1/(6)*(tau_0**3)

        if ORDER == 1:
            # 1st derivative of spline
            db = np.zeros([len(tau),self.degree+1]) 
            db[:,0] = 1/(6)*(-3*tau_3**2 + 24*tau_3 - 48 ) * (1./self.knot_space) 
            db[:,1] = 1/(6)*(9*tau_2**2 - 48*tau_2 + 60 ) * (1./self.knot_space)
            db[:,2] = 1/(6)*(-9*tau_1**2 + 24*tau_1 - 12) * (1./self.knot_space)
            db[:,3] = 1/(6)*(3*tau_0**2) * (1./self.knot_space)
            return b, db
        else:
            return b, -1 

    """"Compute spline tensor coefficients - 2D function """
    def compute_tensor_spline(self, pts, ORDER=0):
        # Storing number of points
        nb_pts = pts.shape[1]

        # Compute spline along each axis
        bx, dbx = self.compute_spline(pts[0,:], self.grid_center[0,0], ORDER)
        by, dby = self.compute_spline(pts[1,:], self.grid_center[1,0], ORDER)

        # Compute spline tensor
        B = np.zeros([nb_pts,(self.degree+1)**2])
        for i in range(0,self.degree+1):
            for j in range(0,self.degree+1):           
                B[:,i*(self.degree+1)+j] = by[:,i]*bx[:,j]


        if ORDER ==1:
            dBx = np.zeros([nb_pts,(self.degree+1)**2])
            dBy = np.zeros([nb_pts,(self.degree+1)**2])        
            for i in range(0,self.degree+1):
                for j in range(0,self.degree+1):           
                    dBx[:,i*(self.degree+1)+j] = by[:,i]*dbx[:,j]
                    dBy[:,i*(self.degree+1)+j] = dby[:,i]*bx[:,j]
            return B, dBx, dBy

        return B, -1

    """"Update the control points of the spline map"""
    def update_spline_map(self, pts_occ, pts_free, pose):
        # Free space 
        c_index_free = self.compute_sparse_tensor_index(pts_free)
        c_index_occ = self.compute_sparse_tensor_index(pts_occ)

        B_occ, _ = self.compute_tensor_spline(pts_occ, ORDER=0)
        s_est_occ_ant = np.sum(self.ctrl_pts[c_index_occ]*B_occ, axis=1)

        c_index_occ_free = np.intersect1d(c_index_free, c_index_occ)
        self.ctrl_pts[c_index_free] -= self.logodd_free
        self.ctrl_pts[c_index_occ_free] += .5*self.logodd_free

        #Occupied space 
        # for i in range(0, pts_occ.shape[1]):
        #     if i < pts_occ.shape[1]-1:
        #         d = np.linalg.norm(pts_occ[:,i+1] - pts_occ[:,i])    
        #     else:
        #         d = np.linalg.norm(pts_occ[:, i] - pts_occ[:, i-1])
        #     d = (min(d/(4*self.knot_space),1))
        #     s_est_occ = np.sum(self.ctrl_pts[c_index_occ[i,:]]*B_occ[i,:])   
        #     e_occ = min(self.logodd_max_occupied, (s_est_occ_ant[i] + self.logodd_occupied))-s_est_occ 
        #     B_occ_norm = np.linalg.norm(B_occ[i,:])
        #     B_occ_norm_squared = B_occ_norm**2
        #     mag_occ =  e_occ /B_occ_norm_squared
        #     np.add.at(self.ctrl_pts, c_index_occ[i,:], d*(B_occ[i,:]*mag_occ))

        s_est_occ = np.sum(self.ctrl_pts[c_index_occ]*B_occ, axis=1)   
        e_occ = (self.logodd_max_occupied - s_est_occ) 
        B_occ_norm = np.linalg.norm(B_occ, axis=1)
        B_occ_norm_squared = B_occ_norm**2
        mag_occ =  np.minimum(self.logodd_occupied/B_occ_norm_squared, np.abs(e_occ)) * np.sign(e_occ)
        np.add.at(self.ctrl_pts, c_index_occ, (B_occ.T*mag_occ).T)        

        # Control points index 
        c_index_min = min(np.min(c_index_occ[:,0]), np.min(c_index_free[:,0]))
        c_index_max = max(np.max(c_index_occ[:,-1]), np.max(c_index_free[:,-1]))    
        self.ctrl_pts[c_index_min:c_index_max+1] = np.maximum(np.minimum(self.ctrl_pts[c_index_min:c_index_max+1], self.logodd_max_occupied), self.logodd_min_free)


    """ Evaluata map """
    def evaluate_map(self, pts):
        B, _ = self.compute_tensor_spline(pts)
        c_index = self.compute_sparse_tensor_index(pts)
        s = np.sum(self.ctrl_pts[c_index]*B, axis=1)
        return s

    """"Occupancy grid mapping routine to update map using range measurements"""
    def update_map(self, pose, ranges):
        # Removing spurious measurements
        tic = time.time()
        ranges_occ, angles_occ = self.remove_spurious_measurements(ranges)
        self.time[0] += time.time() - tic
        # Converting range measurements to metric coordinates
        tic = time.time()
        pts_occ_local = self.range_to_coordinate(ranges_occ, angles_occ)
        self.time[1] += time.time() - tic
        # Detecting free cells in metric coordinates
        tic = time.time()
        pts_free_local  = self.detect_free_space(ranges)
        self.time[2] += time.time() - tic
        # Transforming metric coordinates from the local to the global frame
        tic = time.time()
        pts_occ = self.local_to_global_frame(pose,pts_occ_local)
        pts_free = self.local_to_global_frame(pose,pts_free_local)
        self.time[3] += time.time() - tic
        # Compute spline
        tic = time.time()
        self.update_spline_map(pts_occ,  pts_free, pose)
        self.time[4] += time.time() - tic
        
