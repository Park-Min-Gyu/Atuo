import gmsh, sys, time
size=float(sys.argv[1]); out=sys.argv[2]; src=sys.argv[3]
t0=time.time(); gmsh.initialize(); gmsh.option.setNumber("General.Terminal",1); gmsh.option.setNumber("General.Verbosity",2)
gmsh.model.occ.importShapes(src); gmsh.model.occ.synchronize()
gmsh.option.setNumber("Mesh.MeshSizeMax",size); gmsh.option.setNumber("Mesh.MeshSizeMin",0.3)
gmsh.option.setNumber("Mesh.MeshSizeFromCurvature",0); gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary",1)
gmsh.option.setNumber("Mesh.ElementOrder",1); gmsh.option.setNumber("Mesh.Optimize",1); gmsh.option.setNumber("Mesh.OptimizeNetgen",1)
gmsh.option.setNumber("Mesh.Algorithm",6); gmsh.option.setNumber("Mesh.Algorithm3D",1); gmsh.option.setNumber("General.NumThreads",2)
gmsh.model.addPhysicalGroup(3,[t for d,t in gmsh.model.getEntities(3)],name="RUBBER")
gmsh.model.mesh.generate(3); gmsh.option.setNumber("Mesh.SaveGroupsOfElements",1); gmsh.write(out); print("DONE",time.time()-t0)
