import numpy as np
import random
import math
import matplotlib.pyplot as plt
import json
import random
import inspect
import requests
#-----------------------------------------------------------------------------------------------------------------   
global num_features
global point
global node_IP_addresses
global rtts_matrix

#----------------------- Global variable ---------------------------
max_iter = 10
num_features =200
num_agents=30
point = []
node_IP_addresses=[]
node_name=[]

#-------------------------------------------------------------------------------------------------------   
def extract_number(x):
    try:
        s=''
        start=0
        for i in range(len(x)):
            if ord(x[i])>=48 and ord(x[i])<58 or x[i]=='.':
                s=s+x[i]
                start=1
            if start==1 and (x[i]==' ' or x[i]=='\n'):
                    return s    
        return s
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#-------------------------------------------------------------------------------------------------------   

def load_setup_data(nlines):
    try:
        res=[]
        with open("clustering_setup.txt", "r") as file:
            for i in range(nlines):
                line1 = file.readline()
                y=extract_number(line1)
                res.append(y)
        return res 
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#-------------------------------------------------------------------------------------------------------   
#  Read initial data from data.json 

def Read_node_data_from_anjem_file():
    global point
    global node_IP_addresses
    global node_name
    global num_features
    global rtts_matrix

    # Read Anjem file here
    try:
        # Try to open and load the JSON file
  
        #with open("data.json", "r") as f:
        #    nodes = json.load(f)

        response=requests.get("http://localhost:4040/cluster-status")
        nodes=response.json()
        
        node_name=[]
        addr=[]
        position=[]
        counter=0
        for node in nodes:
            node_name.append(node.get("name"))
            addr.append(node.get("addr"))
            position.append(node.get("coordinate", {}).get("Vec"))
            counter=counter+1
        node_IP_addresses=addr

        rtts_matrix = {}
        for node in nodes:
            source = node['name']
            rtts_matrix[source] = node.get('rtts', {})
            #print(rtts_matrix[source])
        #x=input()
        
        for source, destinations in rtts_matrix.items():
            for dest, rtt in destinations.items():
                rtts_matrix[source][dest]=rtt


        point= list(map(list, zip(*position)))
        print("\nNumber of records in input file:", counter) 
        #point = [[math.floor(item * 1000000) for item in row] for row in point]

        print("-" * 60) 
        print(f"{'Index':<6} {'Point':<20} {'Name':<25} {'IP Address'}")
        print("-" * 60)
        for i in range(counter):
            point_str = f"[{point[0][i]}, {point[1][i]}]"
            print(f"{i:<6} {point_str:<20} {node_name[i]:<25} {node_IP_addresses[i]}")
        print("\n")

        num_features=counter 
        return counter
            
    except FileNotFoundError:
        print("Error: The file 'nodes.json' was not found. Please check the file path and try again.")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON. Make sure the file contains valid JSON.")
    
#-------------------------------------------------------------------------------------------------------   
# Sigmoid function for binary update
def sigmoid(x):
    return 1 / (1 + np.exp(-x))

# ===================================================================================

def fitness_function(solution, latency_threshold):

    try: 
        dist=[] 
        for i in range(num_features):
            dist.append(10000)
  
        ch=[]
        for i in range(num_features):
            ch.append(-1)
    
        ave=0.0
        count_all=0

        #=================================================
        #  Finding the nearest CH to each node        
        for i in range(len(solution)):
            min1=100000
            if solution[i]==0:
                min_node=10000
                min_dist=10000

                for j in range(len(solution)):
                    if solution[j]==1:
                        a=node_name[i]
                        b=node_name[j]
                        y=rtts_matrix[a][b]
                        if y< latency_threshold  and y<min_dist:
                            min_node=j
                            min_dist=y
                            #print("=== ", node_name[i],"==>",node_name[min_node],"=", y )      
                            
                ch[i]=min_node
                dist[i]=min_dist
        #print(solution)
        N= len(solution)
        
        for i in range(N):
            if solution[i]==1:
                count = ch.count(i)
                #print(count)
                if count==0:
                   mind=10000
                   mini=10000
                   for j in range(N):
                       if i!=j and solution[j]==1:
                           a=node_name[i]
                           b=node_name[j]
                           y=rtts_matrix[a][b]
                           if y< latency_threshold  and y<mind:
                               mini=j
                               mind=y
                   if mind!=10000:         
                       ch[i]=mini
                       solution[i]=0
                       dist[i]=mind

        #x= input("aaaaa")                        
                    
                #    print("---- ", node_name[i],"==>",node_name[ch[i]],"=", dist[i] )
               # else:
               #     print("#### ", node_name[i],"==>",ch[i],"=", dist[i] )
                
                            

   #     print(dist)
   #     print(ch)
   #     print(node_name)
   #     print()
   #     for i in range(len(solution)):
   #          if solution[i]==0:
   #              if ch[i]==10000:
   #                  print(node_name[i],"==> No CH")                        
   #              else:    
   #                  print(node_name[i],"==>",node_name[ch[i]],"=", dist[i] )      
            
    #    x=input()
        # compute intracluster distance                    
        s2=0
        c1=0
        for i in range(len(solution)):
            if ch[i]!=-1:
                s2=s2+dist[i]
                c1=c1+1          
        if c1!=0:           
            ave=s2/c1

        # Find number of alone node with no CH       
        counter=0
        for i in range(len(solution)):
            if solution [i]==0 and ch[i]==-1:
                counter=counter+1

        fit=(sum(solution)/len(solution)) +(counter/len(solution))+(ave/latency_threshold)

        return fit,ch,dist

    except Exception as e:
        print("")
        #current_function = inspect.currentframe().f_code.co_name
        #line_number = inspect.currentframe().f_lineno        
       # print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")



#=================================================================================
# Binary Grey Wolf Optimizer
def binary_gwo(num_agents, num_features, max_iter, latency_threshold):
    # Initialize the positions of wolves (binary vectors)

    ch1=[]
    ch2=[] 
    dist1=[]
    dist2=[]
    for i in range(num_features):
        ch1.append(0)
        ch2.append(0)
        dist1.append(0)
        dist2.append(0)


    wolves = np.random.randint(2, size=(num_agents, num_features))
    
    # Initialize alpha, beta, delta wolves (best solutions)
    alpha_pos = np.zeros(num_features)
    alpha_score = float("inf")
    
    beta_pos = np.zeros(num_features)
    beta_score = float("inf")
    
    delta_pos = np.zeros(num_features)
    delta_score = float("inf")

    # Main optimization loop
    for t in range(max_iter):
        # Evaluate fitness of each wolf
 
        #aa=round(max_iter/100)
        #if i mod aa==0:
        print("#",end='', flush=True)
        for i in range(num_agents):
            fitness,ch1,dist1 = fitness_function(wolves[i],latency_threshold)

            # Update alpha, beta, delta
            if fitness < alpha_score:
                delta_score, delta_pos = beta_score, beta_pos.copy()
                beta_score, beta_pos = alpha_score, alpha_pos.copy()
                alpha_score, alpha_pos = fitness, wolves[i].copy()
                ch2=ch1
                dist2=dist1

            elif fitness < beta_score:
                delta_score, delta_pos = beta_score, beta_pos.copy()
                beta_score, beta_pos = fitness, wolves[i].copy()
            elif fitness < delta_score:
                delta_score, delta_pos = fitness, wolves[i].copy()

        # Coefficients
        a = 2 - 2 * t / max_iter  # linearly decreases from 2 to 0

        for i in range(num_agents):
            for j in range(num_features):
                r1, r2 = random.random(), random.random()
                A1 = 2 * a * r1 - a
                C1 = 2 * r2

                D_alpha = abs(C1 * alpha_pos[j] - wolves[i][j])
                X1 = alpha_pos[j] - A1 * D_alpha

                r1, r2 = random.random(), random.random()
                A2 = 2 * a * r1 - a
                C2 = 2 * r2

                D_beta = abs(C2 * beta_pos[j] - wolves[i][j])
                X2 = beta_pos[j] - A2 * D_beta

                r1, r2 = random.random(), random.random()
                A3 = 2 * a * r1 - a
                C3 = 2 * r2

                D_delta = abs(C3 * delta_pos[j] - wolves[i][j])
                X3 = delta_pos[j] - A3 * D_delta

                # Aggregated position update
                X = (X1 + X2 + X3) / 3

                # Binary position update with sigmoid and probability threshold
                wolves[i][j] = 1 if sigmoid(X) > random.random() else 0

    #print(alpha_pos, alpha_score,ch2)
    return alpha_pos, alpha_score,ch2, dist2
#==========================================================================
# Example usage

def clustering_BGWO(latency_threshold):
    global num_features
    global num_agents
 #   global latency_threshold
    global max_iter

    #===================   Loading Global variables
    setup_file_lines=2
    x=load_setup_data(setup_file_lines)
    max_iter=int(x[0]) 
    num_agents=int(x[1])  # seconds
    
    print("Loading data from clustering_setp.txt ...")
    print(f"max_iter = {max_iter}")
    print(f"num_agents = {num_agents}\n")

    #----------------------------------
    N_node=Read_node_data_from_anjem_file()   #  <<<<<======= hello
    print("Total number of records fetched from anjem's p2p system: ",N_node,"\n")
    #---------------------------------
    members=[]
    for i in range(num_features):
        members.append(0)

    print("Running clustering...",flush=True)
    best_solution, best_fitness,ch,dist = binary_gwo(num_agents, num_features, max_iter,latency_threshold)



  #==================================================================
    for i in range(num_features):
        print("\n\n")
        for j in range(num_features):
            if(i!=j):
                print("distance between node: "+node_name[i]+" and node:",node_name[j],"    ", rtts_matrix[node_name[i]][node_name[j]])


  #==================================================================
 #   print("best=",best_solution)
    for i in range(len(best_solution)):
        if ch[i]!=-1:
            members[ch[i]]=members[ch[i]]+1      

#   print("\n\n\n\n\n\n\n\n")
    final_members=[]
    for i in range(num_features):
        row=[]
        if best_solution[i]==1:
#           print("\n\nch = ", i)
#           print("members: ",end='')
            for j in range(num_features):
                if ch[j]==i:
#                   print(j,'  ',end='')
                    row.append(j)
        if row:          
            final_members.append(row)

    final_ch=[]
    for j in range(num_features):
        if ch[j]==-1:
#           print(j,'  ',end='')
            final_ch.append(j)

#   print("\n  newly created ========================================\n")
#   print(final_ch)

#   print("\n========================================")
#   print(final_members)
#   print("\n----------------------------------------")
#   for i in range(len(final_ch)):
#       print("\n\nch=",final_ch[i])
#       print("======= members")
#       for j in range(len(final_members[i])):
#           print(f"{final_members[i][j]:5}",end='')
           

    return final_ch,final_members, node_IP_addresses,node_name,dist
