import numpy as np
import pandas as pd

class RunningStat(object):
    '''
    Keeps track of first and second moments (mean and variance)
    of a streaming time series.
     Taken from https://github.com/joschu/modular_rl
     Math in http://www.johndcook.com/blog/standard_deviation/
    '''
    def __init__(self, shape):
        self._n = 0
        self._M = np.zeros(shape)
        self._S = np.zeros(shape)
    def push(self, x):
        x = np.asarray(x)
        assert x.shape == self._M.shape,(x.shape,self._M.shape)
        self._n += 1
        if self._n == 1:
            self._M[...] = x
        else:
            oldM = self._M.copy()
            self._M[...] = oldM + (x - oldM) / self._n
            self._S[...] = self._S + (x - oldM) * (x - self._M)
    @property
    def n(self):
        return self._n
    @property
    def mean(self):
        return self._M
    @property
    def var(self):
        return self._S / (self._n - 1) if self._n > 1 else np.square(self._M)
    @property
    def std(self):
        return np.sqrt(self.var)
    @property
    def shape(self):
        return self._M.shape

class ZFilter:
    """
    y = (x-mean)/std
    using running estimates of mean,std
    """
    def __init__(self, shape, center=True, scale=True, clip=None,gamma=None):
        assert shape is not None
        self.center = center
        self.scale = scale
        self.clip = clip
        self.shape = shape
        self.rs = RunningStat(self.shape)
        self.gamma=gamma
        if gamma:
            self.ret = np.zeros(shape)

        
        # self.prev_filter = prev_filter

    def __call__(self, x, **kwargs):
        # x = self.prev_filter(x, **kwargs)
        # print(x)
        if self.gamma:
            self.ret = self.ret * self.gamma + x
            self.rs.push(self.ret)
        else:
            self.rs.push(x)
        if self.center:
            x = x - self.rs.mean
        if self.scale:
            if self.center:
                x = x / (self.rs.std + 1e-8)
            else:
                diff = x - self.rs.mean
                diff = diff/(self.rs.std + 1e-8)
                x = diff + self.rs.mean
                # x = x/(self.rs.std + 1e-8)
        if self.clip:
            x = np.clip(x, -self.clip, self.clip)
        return x

    def reset(self):
        # self.prev_filter.reset()
        if self.gamma:
            self.ret = np.zeros_like(self.ret)
        self.rs = RunningStat(self.shape)

class Identity:
    '''
    A convenience class which simply implements __call__
    as the identity function
    '''
    def __call__(self, x, *args, **kwargs):
        return x

    def reset(self):
        pass
    
def test_r():
    r_filter = ZFilter(shape=(), center=False)

    r_list = []
    for r in range(10):
        rew = r_filter(r)
        print(r,rew)
    
    r_filter.reset()
    for r in range(10):
        rew = r_filter(r)
        print(r,rew)

class NeighbourAgentBuffer(object):
    def __init__(self,state_shape,hist_length=5,future_length=5,query_mode='full_future'):
        self.hist_length = hist_length
        self.future_length = future_length

        self.query_mode = query_mode
        assert self.query_mode in {'full_future','default','history_only'}
        #full future(the neighbors must have full future length steps given current timesteps)
        #TODO:default(the neighbors must have at least 1 future step)(involves using mask in calculating loss)
        self.buffer = dict()
        self.state_shape = state_shape

    
    def add(self,ids,values,timesteps):

        if ids not in self.buffer:
            self.buffer[ids]={
                'values':[],
                'timesteps':[]
            }

        # for easier query
        if len(self.buffer[ids]['timesteps'])>0:
            if timesteps!=self.buffer[ids]['timesteps'][-1]+1:
                # print(self.buffer[ids]['timesteps'][-1],timesteps)
                id_arr = np.arange(self.buffer[ids]['timesteps'][-1],timesteps+1)
                fp = np.array([self.buffer[ids]['values'][-1],values])

                res = []
                for i in range(fp.shape[1]):
                    inter = np.interp(id_arr[1:-1],[id_arr[0],id_arr[-1]],fp[:,i])
                    res.append(inter)
                res = np.transpose(res)
                
                for t,v in zip(id_arr[1:-1],res):
                    self.buffer[ids]['values'].append(v)
                    self.buffer[ids]['timesteps'].append(t)
                
                # print(self.buffer[ids]['timesteps'][-1])

            assert timesteps==self.buffer[ids]['timesteps'][-1]+1,('this_time steps:',timesteps,'last:',self.buffer[ids]['timesteps'][-1],ids)
        self.buffer[ids]['values'].append(values)
        self.buffer[ids]['timesteps'].append(timesteps)
    
    def query_futures(self,curr_timestep,curr_ids,pad_length=10):

        neighbor_val = []
        for ids in curr_ids:
            candidate_neighbor = self.buffer[ids]
            hist_t,fut_t = curr_timestep - candidate_neighbor['timesteps'][0] + 1,candidate_neighbor['timesteps'][-1]-curr_timestep
            n = max(hist_t-self.hist_length,0)
            l = min(hist_t,self.hist_length)
            f = min(fut_t,self.future_length)
            fut = candidate_neighbor['values'][n+l:n+l+f]
            fut = self.pad_fut(fut, pad_length)
            neighbor_val.append(fut)
        neighbor = neighbor_val 

        return np.array(neighbor,dtype=np.float32)

    def query_neighbours(self,curr_timestep,curr_ids,curr_ind,keep_top=5,pad_length=10):
        neighbor_val = []
        i=0
        buf_ind=[]
        for ids,ind in zip(curr_ids,curr_ind):
            candidate_neighbor = self.buffer[ids]
            hist_t,fut_t = curr_timestep - candidate_neighbor['timesteps'][0] + 1,candidate_neighbor['timesteps'][-1]-curr_timestep
            n = max(hist_t-self.hist_length,0)
            l = min(hist_t,self.hist_length)
            f = min(fut_t,self.future_length)

            if self.query_mode=='history_only':
                if hist_t<=0:
                    continue
                val = candidate_neighbor['values'][n:n+l]
                # if l==1:
                #     val = np.expand_dims(val, axis=0)
                # print(np.array(val).shape)
                val = self.pad_hist(val,pad_length)
                # print(len(val),n,l)
                neighbor_val.append(val)
                buf_ind.append(ind)
                i+=1
            elif self.query_mode=='full_future':
                if fut_t<self.future_length:
                    continue
                hist = candidate_neighbor['values'][n:n+l]
                fut = candidate_neighbor['values'][n+l:n+l+f]
                hist = self.pad_hist(hist,pad_length)
                neighbor_val.append(hist+fut)
                i+=1    
            else:
                raise NotImplementedError()
            
            if i>=keep_top:
                break
        
        pad_num = keep_top - min(len(neighbor_val),keep_top)


        pad_val = np.zeros((np.clip(curr_timestep+1,0,self.state_shape[1]),self.state_shape[2]))
        # print(pad_val.shape,self.state_shape,curr_timestep)
        # print(neighbor_val[0].shape)

        neighbor = neighbor_val + pad_num*[pad_val]

        # print(neighbor)

        return neighbor,buf_ind

    def pad_fut(self,line,pad_length):
        assert len(np.array(line).shape)==2,(line)
        num = pad_length - min(pad_length,len(line))
        padded = line + [[0]*len(line[-1])]*num  
        return padded

    def pad_hist(self,line,pad_length):
        assert len(np.array(line).shape)==2,(line)
        num = pad_length - min(pad_length,len(line))
        padded = [[0]*len(line[0])]*num + line      
        return padded
    
    def clear(self):
        self.buffer = dict()

def split_future(egos,future_steps=10):
    res= []
    masks = []
    for i in range(egos.shape[0]):
        line = egos[i:i+future_steps]
        mask = [1]*line.shape[0] + [0]*(future_steps-line.shape[0])
        if line.shape[0]<future_steps:
            zeros = np.zeros((future_steps-line.shape[0],line.shape[-1]))
            line = np.concatenate((line,zeros),axis=0)
        
        res.append(line)
        masks.append(mask)
    
    return np.array(res) , np.array(masks)

def test_df():
    data = [8,9,10,11,12,13,14,15,16]
    t = 10
    hist,future = t-data[0]+1,data[-1]-t
    n = max(hist-10,0)
    l = min(hist,10)
    f = min(future,30)
    print(data[n:n+l])
    print(data[n+l:n+l+f])

from envision.client import Client as Envision
from smarts.core.scenario import Scenario
from smarts.core.sumo_road_network import SumoRoadNetwork
import numpy as np
import pickle
import argparse
import matplotlib.pyplot as plt
from tqdm import tqdm
import pickle

def decode_map_xml(path):
    network = SumoRoadNetwork.from_file(path)
    graph = network.graph
    lanepoints = network._lanepoints
    nodes = graph.getNodes()
    # print(nodes)
    # print(graph.getEdges())
    # polys = []
    # # print(len(graph.getEdges()))
    # for edge in graph.getEdges():
    #     poly = []
    #     # print(len(edge.getLanes()))
    #     for lane in edge.getLanes():
    #         shape = SumoRoadNetwork._buffered_lane_or_edge(lane, lane.getWidth())
    #         # shape = lane.getShape()
    #         # print(lane.getID())
    #         # print(lane.getParams())
    #         # print(lane.getNeigh())
    #         # print(lane.getWidth())
    #         # print(lane.getBoundingBox())
    #         # print(lane.getOutgoing())
    #         # print(lane.getIncoming())
    #         # print(lane.getConnection())
    #         print(shape)
    #         assert 1==0
    #         poly.append(shape)
    #         # Check if "shape" is just a point.
    #         # if len(set(shape.exterior.coords)) == 1:
    #         #     # logging.debug(
    #         #     #     f"Lane:{lane.getID()} has provided non-shape values {lane.getShape()}"
    #         #     # )
    #         #     continue
    #     polys.append(poly)

    polys = network._compute_road_polygons()

    # print(len(polys))
    plt.figure(figsize=(10,10))
    cnt = 0
    for i,poly in enumerate(polys):
        p = poly.exterior.coords
        # for p in poly:
        x,y = [c[0] for c in p],[c[1] for c in p]
        # x,y = make_interp(x, y)
        # print(len(x))
        # plt.scatter(x[0], y[0],edgecolors='black')
        h_alpha =  1
        h_lw = 1.5
        plt.plot(x,y,'--', color='k', linewidth=h_lw, alpha=h_alpha)
        # plt.scatter(x,y,s=10)
        cnt+=len(x)

    plt.savefig('./TPDM_transformer/test_maps/test_map_new.png')
    # print(cnt)
    # return plt

def make_interp(x_value,y_value,min_dist=2):
    interp_x = []
    interp_y = []
    for j in range(len(x_value)-1):
        x_diff = x_value[j+1] - x_value[j]
        y_diff = y_value[j+1] - y_value[j]
        dist=np.sqrt(x_diff**2+y_diff**2)
        if dist<=min_dist:
            interp_x.append(x_value[j])
            interp_y.append(y_value[j])
        else:
            need_interp_num = dist//min_dist
            index = np.arange(2+need_interp_num)
            new_x = np.interp(index,[0,index[-1]],[x_value[j],x_value[j+1]]).tolist()
            new_y = np.interp(index,[0,index[-1]],[y_value[j],y_value[j+1]]).tolist()
            interp_x = interp_x + new_x[:-1] #traj.x_value[j+1] doesnot count
            interp_y = interp_y + new_y[:-1]
        
    interp_x.append(x_value[-1])
    interp_y.append(y_value[-1])

    return interp_x,interp_y

def process_map(path):
    network = SumoRoadNetwork.from_file(path)
    graph = network.graph
    lanepoints = network._lanepoints
    nodes = graph.getNodes()
    polys = []
    for edge in tqdm(graph.getEdges()):
        poly = []
        for lane in edge.getLanes():
            shape = lane.getShape()
            width = getWidth()
            ID = lane.getID()
            x,y = [s[0] for s in shape],[s[1] for s in shape]
            x,y = make_interp(x, y)
            poly.append([x,y,width,ID])
        polys.append(poly)

if __name__=="__main__":
    decode_map_xml('./SMARTS/scenarios/left_turn_new/map.net.xml')
