import gmsh, numpy as np, time
t0=time.time()
gmsh.initialize(); gmsh.option.setNumber("General.Terminal",0)
gmsh.model.occ.importShapes("/home/user/.perplexity/attachments/f95e160de2d34b0fb0f263ac093caa9f/Test_2026.stp")
gmsh.model.occ.synchronize()
v0=gmsh.model.occ.getMass(3,1)
bases=[47,1076,3413,3434,131,1091,1042]
# planes near x=±24 (dot faces)
for d,t in gmsh.model.getEntities(2):
    bb=gmsh.model.getBoundingBox(d,t)
    if abs(bb[0]-bb[3])<1e-3 and abs(abs(bb[0])-24)<0.8: print("plane24",t,bb[0],gmsh.model.occ.getMass(2,t))
sph=[t for d,t in gmsh.model.getEntities(2) if gmsh.model.getType(2,t)=="Sphere" and gmsh.model.occ.getMass(2,t)<1]
xs=[gmsh.model.occ.getCenterOfMass(2,t)[0] for t in sph]; print("sphere cx", np.unique(np.round(xs,2)))
tools=[]
for b in bases:
    loops,curves=gmsh.model.occ.getCurveLoops(b)
    keep=[]
    for L,cs in zip(loops,curves):
        bbs=np.array([gmsh.model.getBoundingBox(1,abs(c)) for c in cs])
        ext=max(bbs[:,4].max()-bbs[:,1].min(), bbs[:,5].max()-bbs[:,2].min())
        if ext>4.0: keep.append(list(cs))
    cl=[gmsh.model.occ.addCurveLoop([abs(c) for c in cs]) for cs in keep]
    s=gmsh.model.occ.addPlaneSurface(cl)
    e=gmsh.model.occ.extrude([(2,s)],0.3,0,0)
    tools+= [x for x in e if x[0]==3]
    print("base",b,"loops",len(loops),"kept",len(keep))
gmsh.model.occ.synchronize()
r,_=gmsh.model.occ.cut([(3,1)],tools,removeTool=True)
gmsh.model.occ.synchronize()
print("result",r)
v1=sum(gmsh.model.occ.getMass(3,t) for d,t in gmsh.model.getEntities(3))
print("faces",len(gmsh.model.getEntities(2)),"vol",v0,v1,100*(v1-v0)/v0)
gmsh.write("cut1.brep"); print("t",time.time()-t0)
