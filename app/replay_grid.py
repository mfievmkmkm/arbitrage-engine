import itertools
def combinations(edges,targets,trailings,time_stops,sizes):
 return [{"min_edge":a,"target":b,"trailing":c,"time_stop":d,"size":e} for a,b,c,d,e in itertools.product(edges,targets,trailings,time_stops,sizes)]
