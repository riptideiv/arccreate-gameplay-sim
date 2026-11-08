"""Shared wrist-angle convention and bounded static glove-pose estimate.

Pure NumPy: also imported by Blender's actual pose solver. The planner estimate
has no temporal IK, airborne hand, or forearm collision claim.
"""
import numpy as np

NEUTRAL = np.array(((10.,-18.),(10.,18.)))
HOME_XY = np.array(((-.085,-.068),(.085,-.068)))
SHOULDERS = np.array(((-.18,-.38),(.18,-.38)))


def bases(angles):
    p,y=np.moveaxis(np.radians(angles),-1,0)
    cp,sp,cy,sy=np.cos(p),np.sin(p),np.cos(y),np.sin(y)
    out=np.zeros(angles.shape[:-1]+(3,3))
    out[...,0,0]=cy;out[...,0,1]=-sy*cp;out[...,0,2]=-sy*sp
    out[...,1,0]=sy;out[...,1,1]=cy*cp;out[...,1,2]=cy*sp
    out[...,2,1]=-sp;out[...,2,2]=cp
    return out


def natural_angles(points):
    """Pitch/yaw of relaxed hands aimed toward fixed body-side arm anchors.

    Calibrated so idle positions have the existing neutral glove orientation.
    Points may be (...,2,2/3); the camera does not enter this calculation.
    """
    q=np.asarray(points)[...,:2]
    ray=SHOULDERS-q
    home=SHOULDERS-HOME_XY
    yaw=np.degrees(np.arctan2(ray[...,0],-ray[...,1]))
    baseline=np.degrees(np.arctan2(home[:,0],-home[:,1]))
    out=np.zeros(q.shape)
    out[...,0]=NEUTRAL[:,0]
    out[...,1]=NEUTRAL[:,1]+yaw-baseline
    return out


def deviations(angles,points):
    d=np.asarray(angles)-natural_angles(points)
    d=d.copy();d[...,1]=(d[...,1]+180)%360-180
    return d


def strain(angles,points,free_deg=30):
    d=np.abs(deviations(angles,points))
    return np.sum((np.maximum(0,d-free_deg)/90)**2,axis=(-2,-1))


def metrics(points,angles,active,free_deg=30):
    d=deviations(angles,points);mask=np.asarray(active,dtype=bool)
    raw=np.linalg.norm(d,axis=-1)[mask]
    effort=np.sum((np.maximum(0,np.abs(d)-free_deg)/90)**2,axis=-1)
    return {'free_deg':free_deg,'active_peak_deg':float(raw.max(initial=0)),
        'active_rms_deg':float(np.sqrt(np.mean(raw**2))) if raw.size else 0.,
        'active_strain_s':float(effort[mask].sum()/60),
        'definition':'pitch/yaw deviation from body-side natural heading; not anatomical roll or forearm IK'}


class WristEstimate:
    """Cached 5 mm static-pose estimate using the renderer's glove body spheres.

    A small pose catalogue selects clearance first, then least wrist strain.
    Residual overlap is finite, never a note rejection. Actual poses retain the
    full geometry and temporal speed limits downstream.
    """
    def __init__(self,parts,free_deg=30):
        if not parts:raise ValueError('wrist twist planning requires glove proxy geometry')
        self.free_deg=free_deg;self.cache={}
        # Same orientation convention; a bounded subset of the full catalogue.
        cat=np.array([(p,y) for p in (10,50,85) for y in (-150,-90,-30,-18,0,18,30,90,150)],float)
        a,b=np.meshgrid(np.arange(len(cat)),np.arange(len(cat)),indexing='ij')
        self.poses=np.stack((cat[a.ravel()],cat[b.ravel()]),axis=1)
        self.centres=[];self.radii=[];self.floors=[]
        for h,sign in enumerate((1,-1)):
            core=[(n,(sign*x,y,z),rr) for n,(x,y,z),rr in parts if n in ('palm','cuff','index knuckle','folded pinky')]
            if not core:raise ValueError('wrist proxy geometry lacks glove body spheres')
            rot=bases(self.poses[:,h]);c=np.array([p[1] for p in core]);r=np.array([p[2] for p in core])
            centres=np.einsum('nij,pj->npi',rot,c)
            z=np.sqrt(np.einsum('nj,pj->np',rot[:,2,:]**2,r**2))
            self.centres.append(centres);self.radii.append(r.max(axis=1))
            self.floors.append((centres[...,2]+.006-z).min(axis=1))
        self.radius=self.radii[0][:,None]+self.radii[1][None,:]
        self.floor=np.minimum(*self.floors)
        self.query_count=0

    def frame(self,left,right):
        key=tuple(round(x/.005) for q in (left,right) for x in q[:2])
        if key not in self.cache:
            self.query_count+=1
            q=np.array(key,dtype=float).reshape(2,2)*.005
            delta=(self.centres[0][:,:,None,:]-self.centres[1][:,None,:,:]).copy()
            delta[...,:2]+=q[0]-q[1]
            clearance=(np.linalg.norm(delta,axis=-1)-self.radius).min(axis=(1,2))
            overlap=np.maximum(0,.001-clearance)**2+4*np.maximum(0,-self.floor-.00005)**2
            # Clearance dominates this estimate; no extra collision price is
            # hidden inside the wrist weight. It only prices the selected bend.
            allowed=overlap<=overlap.min()+1e-12
            bend=strain(self.poses,q,self.free_deg)
            self.cache[key]=float(bend[allowed].min())
        return self.cache[key]

    def touch_cost(self,left,right):
        start,stop=max(left.down,right.down),min(left.up,right.up)
        return sum(self.frame(left.xy(f),right.xy(f)) for f in range(start,stop))/60
