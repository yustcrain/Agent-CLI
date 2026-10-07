import heapq

def order_projects(projects):
    projects=list(projects); by_name={}; position={}
    for index,project in enumerate(projects):
        if project.name in by_name: raise ValueError(f"duplicate project: {project.name}")
        by_name[project.name]=project; position[project.name]=index
    for project in projects:
        for dep in project.dependencies:
            if dep not in by_name: raise ValueError(f"missing dependency: {dep}")
    indegree={p.name:len(set(p.dependencies)) for p in projects}
    dependents={p.name:set() for p in projects}
    for project in projects:
        for dep in set(project.dependencies): dependents[dep].add(project.name)
    ready=[(position[name],name) for name,count in indegree.items() if count==0]; heapq.heapify(ready)
    result=[]
    while ready:
        _,name=heapq.heappop(ready); result.append(name)
        for child in dependents[name]:
            indegree[child]-=1
            if indegree[child]==0: heapq.heappush(ready,(position[child],child))
    if len(result)!=len(projects): raise ValueError("cycle in dependency graph")
    return result
