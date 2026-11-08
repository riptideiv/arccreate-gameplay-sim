"""Non-arc note-centering preference on the actual replay coordinates.

Points are charged once per note/press; ground holds integrate elapsed time.
Arcs deliberately contribute zero: TracePreference already owns their cost.
"""
import math

SCALE_M = .020


class NoteCenterPreference:
    def __init__(self, projection, free_m=.005):
        self.projection, self.free_m = projection, free_m
        self.values = {}
        self.centres = {}

    def centre(self, item):
        from .two_finger import target_xy
        key=item.occupation_id
        if key not in self.centres:
            self.centres[key]=target_xy(item,item.start_ms,self.projection)
        return self.centres[key]

    def penalty(self, point, centre):
        return (max(0,math.dist(point,centre)-self.free_m)/SCALE_M)**2

    def measure(self, touch):
        if touch is None:return 0.0
        if touch.id in self.values:return self.values[touch.id]
        jobs={}
        for part in touch.parts:
            o=part.candidate.occupation
            if o.kind in ('tap','sky','hold'):
                jobs.setdefault(o.occupation_id,(o,[]))[1].append((part.start,part.stop))
        total=0.0
        for o,spans in jobs.values():
            centre=self.centre(o)
            if o.kind in ('tap','sky'):
                total += self.penalty(touch.first_xy,centre)
            else:
                # A union of served frames; tick density/duplicate parts cannot
                # multiply the price of holding the same ground note.
                frames=set(f for a,b in spans for f in range(max(a,touch.down),min(b,touch.up)))
                total += sum(self.penalty(touch.xy(f),centre) for f in frames)/60
        self.values[touch.id]=total
        return total

    def metrics(self, touches):
        points=[];holds=[]
        for ft in touches:
            t=ft.touch;jobs={}
            for part in t.parts:
                o=part.candidate.occupation
                if o.kind not in ('tap','sky','hold'):continue
                jobs.setdefault(o.occupation_id,(o,set()))[1].update(
                    range(max(part.start,t.down),min(part.stop,t.up)))
            for o,frames in jobs.values():
                centre=self.centre(o)
                if o.kind in ('tap','sky'):
                    points.append(math.dist(t.first_xy,centre)*1000)
                else:
                    holds.extend(math.dist(t.xy(f),centre)*1000 for f in sorted(frames))
        def stats(values):
            data=sorted(values);n=len(data)
            return {'samples':n,'mean_mm':sum(data)/max(n,1),'median_mm':(data[(n-1)//2]+data[n//2])/2 if n else 0,
                'p90_mm':data[min(n-1,int((n-1)*.9))] if n else 0,'max_mm':max(data,default=0)}
        return {'point_hits':stats(points),'ground_hold_frames':stats(holds),'free_radius_mm':self.free_m*1000,
            'scale_mm':SCALE_M*1000,'arcs_charged':False}
