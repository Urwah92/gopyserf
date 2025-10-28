import random
import inspect
import socket
import time
import json
import bgwo1
from ping3 import ping
import os
import xml.etree.ElementTree as ET
import subprocess
import requests
import subprocess
import re
import sys 

from dataclasses import dataclass
@dataclass

class clustering_result:
    node_name: str=''
    node_IP: str=''
    node_distance:str=''
    role: str=''
    ch_name: str=''
    ch_IP: str=''
    ch_port:str=''
    
struct = [clustering_result() for _ in range(500)]
    
#-----------------------------------------------------------------------------------------------------------------   

global max_latency
global join_retry
global node_port
global cluster_members
global my_socket_timeout
global CH_list_received_from_other_chs
global node_IPs
global my_CH
global my_IP
global ping_retries
#-----------------------------------------------------------------------------------------------------------------   
#   Global variable

CH_list_received_from_other_chs=[]
socket_timeout=5 # only for client socket 
max_latency=10
join_retry=10  # when CH is not accessible, the client sleeps for the time specified by this variable and retires
node_port=0

WAIT_FOR_DESTINATION=3  # default delay for the socket 
cluster_members=[]
ch_list=[]
node_status=0   # 0=Flat network,   1=cluster member  2=CH
joined_to_CH=-1  # when this node is culster member, it is joined to this CH, valid when node_status=1
node_IPs=[]
my_CH=""
my_IP=""
ping_retries=""

# ---- CH-to-CH (WAN) Serf settings ----
WAN_PORT_OFFSET = 6947       # WAN gossip/listen port = CH_port + this
WAN_RPC_OFFSET  = 7947       # optional: unique RPC port if you use RPC

#------------------------------------------------------------------------------------------------------------------   
def to_json(msgID, lst=None):
    # Create a dictionary with the integer and list
    if lst is None:
       data = {
            "msgID": msgID,
       }
    else:
       data = {
            "msgID": msgID,
            "cluster_members": lst
       }
    
    # Convert the dictionary to a JSON string
    return json.dumps(data)
#--------------------------------------------------------------------------------------------------------
def from_json(json_data):
    # Parse the JSON string to a Python dictionary
    try:
        data = json.loads(json_data)
        x=data["msgID"]
        y=data.get("cluster_members", None)
        return x, y

    except json.JSONDecodeError:
        return json_data

    # Extract the integer and list from the dictionary
#-------------------------------------------------------------------------------------------------------   
#   Reads one number from a line in the setup file
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
#   Loading setp.txt data file
def load_setup_data():
    try:
        res=[]
        with open("setup.txt", "r") as file:
            for i in range(6):
                line1 = file.readline()
                y=extract_number(line1)
                res.append(y)
        return res 
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#-------------------------------------------------------------------------------------------------------   
def icmp_ping(host):
    try:

        response = ping(host)
        if response==None:
            print("The host is unreachable or blocked  ")
            return 1000   # the host is unreachable or blocked 
        else:
            print("The host is alive   ")
            return round(response,4)
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#--------------------------------------------------------------------------------------------------------
# 
def ping2(host_to_ping):
    try:
        min_rtt=3000.0
        min_index=-1
        live_chs = []
 
        for i in range(len(host_to_ping)):
            print('\nPinging node ', host_to_ping[i] )
            sum1=0
            for j in range(ping_retries):
                print(f"{j+1}th try...    ",end='')
                sum1=sum1+icmp_ping(host_to_ping[i])
            y=sum1/ping_retries
            if y<1000:
                print("RTT = ", y)
            if y<min_rtt:
                min_rtt=y
                min_index=i
            if y<1000:
                live_chs.append(host_to_ping[i])

        print("\nReceived CHs list: ",host_to_ping)
        print("Live CHs are: ",live_chs)
        print("\nPinging CHs is over ")
        print("-----------------------------------------------------")
    
        return min_rtt, host_to_ping[min_index] ,live_chs      
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#---------------------------------------------------------------------------------------------------------------
# it joins to a ch on port=6000 , it must be changedd to 5000 on final version

def join_CH(nearest_ch):
    try:
        msg = "300"
        print("Contacting CH", nearest_ch, " by sending msgID=",msg, flush=True)
                        
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_socket.settimeout(my_socket_timeout) # it raises an exception in 8 seconds
        client_socket.sendto(msg.encode(), (nearest_ch, 5000)) # <==== delete
#        client_socket.sendto(msg.encode(), (nearest_ch, node_port)) # <==== enable this

        response, _ = client_socket.recvfrom(1024)
        #print(response)
        msgID, mem=from_json(response)

        return msgID, mem
    except socket.timeout:   
        print("❌ The host is alive, because already pinged, but the peer program does not respond")
        msgID= 340 # join error
        return msgID, '' 

    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")
        msgID= 340 # join error
        return msgID, '' 
#---------------------------------------------------------------------------------------------------------------
def node_join(nearest_ch, join_retry):   
#   retry joining a client node to the CH and dislay proper output
#   Response from destination:  
#                             350: Accept join request
#                             340: Cannot connect to detination    
#                             370: Already joined 
#                             380: node is alive but is not CH 
    try:
        success=0
        print("\nTrying to join the Cluster head: ", nearest_ch)
        i=1
        while i<=join_retry:
            print(f"\n{i}th try for joining the CH:", nearest_ch, flush=True)
            msgID, mem =join_CH(nearest_ch) 
            if msgID!=340:
               print("Received response :", msgID)
            else:
               print("Error :", msgID)
                   
            if msgID==380: 
                time.sleep(0.01)
                i=i+1                                         
                continue
            else:
                break
        if msgID==380 and i==join_retry:
            print("Cannot joint to the node:", nearest_ch," after ",join_retry," times, because it is not a CH")
        if msgID==350:
            print("\nSucceccsully joined to ",nearest_ch)
            print("\nResponse=",msgID, "\nMembers of CH:",nearest_ch," are:",mem)
        elif msgID==370:
            print("\nI have already joined to this CH")
        
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#---------------------------------------------------------------------------------------------------------------
# This function reads the anjems data from a file
#  read IP address and their distance

def Read_Node_IP_distance():
    try:
        hostname = socket.gethostname()
        nodeIP = socket.gethostbyname(hostname)
        #-- instead of these two lines, you must read from a file 

        N=50
        node_IP_list=[]
        node_distance_list=[]
        for i in range(N):
            node_IP_list.append(nodeIP)

        return node_IP_list,node_distance_list

    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#=======================================================================

def XML_output(final_ch,final_members,node_IPs): 
    try: 
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, "clustering_output.xml")

        root = ET.Element("Table")
        for i in range(len(final_members)):
            record = ET.SubElement(root, "Record")
            int_field = ET.SubElement(record, "Clusterhead")
            int_field.text = node_IPs[final_ch[i]]
            p=''
            for j in range(len(final_members[i])):
                p=p+node_IPs[final_members[i][j]]+','  
    
            string_field = ET.SubElement(record, "Members")
            string_field.text = p

           
          
        i=len(final_members)
        print(i)
        while(i<len(final_ch)):
            print(i)
            record = ET.SubElement(root, "Record")
            int_field = ET.SubElement(record, "Clusterhead")
            int_field.text = node_IPs[final_ch[i]]
            i=i+1 

        tree = ET.ElementTree(root)
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")

#==========================================================================
def send_clustering_result(code,IP1, message):   
    try:

        print("Contacting node: ", IP1, flush=True)
        message=code+message
        print(message)
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_socket.settimeout(1) # it raises an exception in 8 seconds
        client_socket.sendto(message.encode(), (IP1, node_port)) # <==== enable this
        response, _ = client_socket.recvfrom(1024)
        print(response.decode())
        print()
        
    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno        
        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")
        print()

#-----------------------------------------------------------------------------------------------------------

def run_cmd(cmd):
    print(f"Running: {cmd}", flush=True)
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed: {cmd}\nExit code: {e.returncode}")

#-----------------------------------------------------------------------------------------------------------


def join_wan_cluster(ip, my_wan_port, wan_rpc):
    """
    base_ports: list of each CH's LAN port (CH_port). We derive WAN port via WAN_PORT_OFFSET.
    """
    print(f"Joining CH-WAN: {ip}:{my_wan_port}")
    #run_cmd(["./serf", "join", f"-rpc-addr=127.0.0.1:{wan_rpc}", f"{ip}:{my_wan_port}"])
    run_cmd(
    f" ./serf join "
    f"-rpc-addr=127.0.0.1:{wan_rpc} "
    f" {ip}:{my_wan_port}"
    )  
    run_cmd(
    f" ./ch_broker"
    )

#---------------------------------------------------------------------------------------------------------------
def  handling_200(input_str):
    global my_role, target_port, head_ip, hostname, serf_nodeip, wan_chIp # declare globals
    try:
        print(input_str)
        print(hname)            
        fields = [tuple(item.split("=", 1)) for item in input_str.split(",")]

        # Print each value in the style you want
        
        CH_IP=fields[0][1]
        CH_name=fields[1][1] 
        CH_port=fields[2][1]  
        CH_role=fields[3][1]  # 2=CH     1=CLuster_member
        node_IP=fields[4][1]  
        node_name=fields[5][1]
        wan_chIp = fields[6][1]

        print(CH_IP, CH_name, CH_port, CH_role)
        print(node_IP, node_name)     
        print ("Urwah edition: ", wan_chIp)             
 
        if int(CH_role) == 2:
            my_role="head"
            print(f"head")

        else:
            my_role="member"
            print(f"member")
        target_port=CH_port
        head_ip=CH_IP
        serf_nodename = node_IP
 #============================================

# === Add serf binary folder to PATH ===
#       os.environ["PATH"] = "/opt/serfapp:" + os.environ["PATH"]


        target_port = CH_port
        head_ip     = CH_IP
        hostname    = node_name  # Use node_name as Serf node name
        serf_nodeip = node_IP
#        return my_role, target_port, head_ip, hostname

#======================================================================
        print(f"Running")
# === MAIN EXECUTION ===
        os.environ["PATH"] = "/opt/serfapp:" + os.environ["PATH"]
# 1) Leave any existing cluster
        print("Leaving current cluster...", flush=True)
        run_cmd("./serf leave")
        time.sleep(2)

# 2) Kill any existing Serf agents
        print("Killing any existing Serf agents...")
        run_cmd("pkill serf || true")
        time.sleep(1)

# 3) Start this node
        if my_role == "head":
            print("Starting as HEAD node...")
            run_cmd(f"nohup ./serf agent "
                    f"-bind={serf_nodeip}:{target_port} "
                    f"-advertise={serf_nodeip}:{target_port} "
                    f" -node={hostname} > serf_{target_port}.log 2>&1 &")
            print(f"Head node started on port {target_port}")
            time.sleep(5)

            # 2) WAN Serf for CH-to-CH overlay (NEW)
            wan_port = WAN_PORT_OFFSET
            wan_rpc  = WAN_RPC_OFFSET
            print("Starting CH-to-CH (WAN) serf...")
            #if wan_flag is not None:
            #   join_wan_cluster(wan_chIp, wan_port)
            if wan_chIp == serf_nodeip:
                run_cmd(
                    f"nohup ./serf agent "
                    f"-bind={serf_nodeip}:{wan_port} "
                    f"-advertise={serf_nodeip}:{wan_port} "
                    f"-rpc-addr=127.0.0.1:{wan_rpc} "
                    f"-node={hostname}-wan "
                    f"> serf_wan_{wan_port}.log 2>&1 &"
                )
                print(f"WAN serf started on {serf_nodeip}:{wan_port} (rpc {wan_rpc})")
            else:
                run_cmd(
                    f"nohup ./serf agent "
                    f"-bind={serf_nodeip}:{wan_port} "
                    f"-advertise={serf_nodeip}:{wan_port} "
                    f"-rpc-addr=127.0.0.1:{wan_rpc} "
                    f"-node={hostname}-wan "
                    f"> serf_wan_{wan_port}.log 2>&1 &"
                )
                print(f"WAN serf started on {serf_nodeip}:{wan_port} (rpc {wan_rpc})")
                time.sleep(5)
                join_wan_cluster(wan_chIp, wan_port, wan_rpc)
            
            time.sleep(5)
        else:
            time.sleep(10)
            print("Starting as MEMBER node...")
            run_cmd(f"nohup ./serf agent "
                    f"-bind={serf_nodeip}:{target_port} "
                    f"-advertise={serf_nodeip}:{target_port} "
                    f" -node={hostname} > serf_{target_port}.log 2>&1 &")
            print(f"JNode started on port {target_port}")
            time.sleep(5)
            print(f"Joining head node at {head_ip}:{target_port}...")
            run_cmd(f"./serf join {head_ip}:{target_port}")
            print(f"Member joined cluster via {head_ip}:{target_port}")

            
            if not node_name.endswith("serf1"):
                sys.exit()

    except Exception as e:
        current_function = inspect.currentframe().f_code.co_name
        line_number = inspect.currentframe().f_lineno
        print(f"�~]~L Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")
        print()
       
        
#---------------------------------------------------------------------------------------------------------------
def get_serf_node_name():
    try:
        output = subprocess.check_output("./serf info", shell=True).decode()
        inside_agent = False
        for line in output.splitlines():
            if line.strip().startswith("agent:"):
                inside_agent = True
            elif inside_agent and "name =" in line:
                return line.split("=", 1)[1].strip()
    except subprocess.CalledProcessError:
        print("? Failed to get Serf node name. Is Serf running?")
        exit(1)
#---------------------------------------------------------------------------------------------------------------


#---------------------------------------------------------------------------------------------------------------

def start_server(hostIP="", port=5000, hname=""):
#    try:
        global node_status
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.bind((hostIP, port))
        print(f"\nServer started on {hostIP}:{port}. \nWaiting for messages...")
        node_status=0
        #------------------------------
        while True:
            global cluster_members
            global my_CH
            if node_status==0: 
                s="P2P network is flat, node IP is: "+hostIP
            if node_status==1:
                s="P2P network is clustered, and this node < "+ hostIP +" > is a cluster member, its CH is :"+my_CH
            if node_status==2:
                s="P2P network is clustered, and this node is CH: "+hostIP+"\n"
                if len(cluster_members)==0:
                    s=s+"No cluster member"
                else:
                    s=s+"cluster members are: "+str(cluster_members)
            print("\n===========================================================")   
            print(s)   
            print("Waiting for next input message from network ...")
            print("-----------------------------------------------------------\n")   

            message, client_address = server_socket.recvfrom(1024)  # Receive message & detect client address
            print(f"\nRequest is received from {client_address}: {message.decode()}")
            client_ip = client_address[0]   # Extract IP address
            client_port = client_address[1]

            msg_code = int(message[:3])
            message1 = message[3:]
        #---------------------------------------------------   
            if msg_code==100:     # Cluster_init message
                try:
                    node_counter=0

                    nodes_name= []
                    response = "Ack     100 call clustering" 
                    server_socket.sendto(response.encode(), client_address)  # Send reply to detected client
                    # calling BGWO
                    final_ch,final_members,node_IPs,nodes_name, dist=bgwo1.clustering_BGWO(max_latency)
                    print("final_ch", final_ch)
                    
                    anjem_port=4947
                    print("\nClustering output", flush=True)
                    for i in range(len(final_members)):
                        print("=" * 80) 
                        print(f"{i+1}th CLuster     ")
                        
                        CH_PORT=str(anjem_port+i)
                        struct[node_counter].ch_port=CH_PORT
                        print('port=',CH_PORT)
                        
                        
                        print("\nClusterhead ")
                        print("Index           Hostname                    IP address")

                        print("-" * 60) 
                        print(f"{final_ch[i]:<10}    ",end='')
                        print(f"{nodes_name[final_ch[i]]:<30}",end='')
                        #str_anjem=str_anjem+'"'+nodes_name[final_ch[i]]+'", IP:'
                        CH_NAME=nodes_name[final_ch[i]]
                        struct[node_counter].ch_name=nodes_name[final_ch[i]]
                        
                        
                        print(f"{node_IPs[final_ch[i]]:<20}")
                        #
                        CH_IP=node_IPs[final_ch[i]]
                        struct[node_counter].ch_IP=node_IPs[final_ch[i]]
                        struct[node_counter].role=2
                        
                        struct[node_counter].node_IP=CH_IP
                        struct[node_counter].node_name=CH_NAME
                        
                        print("\nCluster Members info")
                        print("Index           Hostname                    IP address            distance")

                        print("-" * 60) 
                        for j in range(len(final_members[i])):
                            node_counter=node_counter+1
                            
                            struct[node_counter].ch_IP=CH_IP
                            struct[node_counter].ch_name=CH_NAME
                            struct[node_counter].role=1
                            struct[node_counter].ch_port=CH_PORT

                            print(f"{final_members[i][j]:<10}    ",end='')
                            print(f"{nodes_name[final_members[i][j]]:<30}",end='')
                            struct[node_counter].node_name=nodes_name[final_members[i][j]]
                            
                            print(f"{node_IPs[final_members[i][j]]:<20}",end="")
                            print(f"{dist[final_members[i][j]]:<20}")
                            struct[node_counter].node_IP=node_IPs[final_members[i][j]]
                            struct[node_counter].node_distance=dist[final_members[i][j]]
                        node_counter=node_counter+1     
                    print('counter=',node_counter)
                    for i in range(node_counter):
                            print(struct[i].ch_IP, "  ", struct[i].ch_name,"  port=", struct[i].ch_port,"  ",  struct[i].role,"  ",struct[i].node_IP, "  ",struct[i].node_name)

                    if len(final_members)==0:
                        zz=0
                    else:
                        zz=i+1    

                    print('\n\nclusters with no member ')  
                    for k in range(zz,len(final_ch)):
                        print("=" * 80) 
                        print(f"{zz+1}th CLuster     ")
                        #str_anjem=str_anjem+str(zz+1)+":{"+chr(13) + chr(10)+"port:"+str(anjem_port+zz)+","+chr(13)+chr(10)+"head:{name:"

                        print("\nClusterhead ")
                        print("Index           Hostname                    IP address")
                        print("-" * 60) 
                        print(f"{final_ch[k]:<10}    ",end='')
                        print(f"{nodes_name[final_ch[k]]:<30}",end='')
                        #str_anjem=str_anjem+'"'+nodes_name[final_ch[k]]+'", IP:'
                        print(f"{node_IPs[final_ch[k]]:<20}")
                        #str_anjem=str_anjem+node_IPs[final_ch[i]]+"},"
                        zz=zz+1 
                    #str_anjem=str_anjem+"}"
                    # Example: head:{...},\n}  → head:{...}\n}
                    #str_anjem = re.sub(r',\s*\n\s*\}', '\n}', str_anjem)
                    # Optionally add wrapping braces to make it look like a single dictionary
                    #str_anjem = "{\n" + str_anjem + "\n}"
                    #print(str_anjem)
                    print("\n") 
                    print("#" * 60) 
                    print("Clustering Results is over\n") 
                    # Collect CH info (names, IPs, base ports) in order of final_ch
                    ch_names = []
                    ch_ips   = []
                    ch_base_ports = []

                    for i in range(len(final_members)):
                        ch_idx = final_ch[i]
                        ch_names.append(nodes_name[ch_idx])
                        ch_ips.append(node_IPs[ch_idx])
                        ch_base_ports.append(str(anjem_port + i))  # This is the CH_PORT you print above

                    # If there are CHs without members, also append them:
                    if len(final_members) < len(final_ch):
                        for k in range(len(final_members), len(final_ch)):
                            ch_idx = final_ch[k]
                            ch_names.append(nodes_name[ch_idx])
                            ch_ips.append(node_IPs[ch_idx])
                            ch_base_ports.append(str(anjem_port + k))

                    iam_CH=0
                    for i in range(len(final_ch)):
                        if node_IPs[final_ch[i]]==hostIP:
                            iam_CH=1
                            my_idx = final_ch.pop(i)
                            final_ch.insert(0, my_idx)
                            
                    if iam_CH==1:
                        node_status= 2  # node is a CH, it must wait to receive join request
                        print("\nNode is a CH")
                        print("\nLet's wait for receiving join request ")
                        # compute my WAN port from my base port (the CH_PORT assigned to me)
                        # find my index in ch_ips
                        #my_idx = next((i for i,(ip) in enumerate(ch_ips) if ip == hostIP), None)
                        '''if my_idx is not None:
                            # 2) WAN Serf for CH-to-CH overlay (NEW)
                            wan_port = WAN_PORT_OFFSET
                            wan_rpc  = WAN_RPC_OFFSET
                            print("Starting CH-to-CH (WAN) serf...")
                            run_cmd(
                                f"nohup ./serf agent "
                                f"-bind={hostIP}:{wan_port} "
                                f"-advertise={hostIP}:{wan_port} "
                                f"-rpc-addr=127.0.0.1:{wan_rpc} "
                                f"-node={wan_hostname}-wan "
                                f"> serf_wan_{wan_port}.log 2>&1 &"
                            )
                            print(f"WAN serf started on {hostIP}:{wan_port} (rpc {wan_rpc})")'''
                    else:
                        node_status= 1
                        XML_output(final_ch,final_members,node_IPs) 
                        print("\n creating XML file")
                    
                    print("Sending clustering results to the network") 
                    #print(str_anjem)
                    print("======================================")
                    print("")     
                    for i in range(len(node_IPs)):  
                        if struct[i].node_IP!=hostIP:
                            str_anjem="ch_ip="+struct[i].ch_IP+",ch_name="+ struct[i].ch_name+",ch_port="+struct[i].ch_port+",role="+str(struct[i].role)+",node_ip="+struct[i].node_IP+",node_name="+struct[i].node_name + ",wan_chIp="+node_IPs[final_ch[0]]
                            send_clustering_result("200",struct[i].node_IP, str_anjem)    
                        else:    
                            mein="ch_ip="+struct[i].ch_IP+",ch_name="+ struct[i].ch_name+",ch_port="+struct[i].ch_port+",role="+str(struct[i].role)+",node_ip="+struct[i].node_IP+",node_name="+struct[i].node_name + ",wan_chIp="+node_IPs[final_ch[0]]
                            handling_200(mein)
                    

                except Exception as e:
                    print("❌ Error in handling message with code 100 : ",e)
           
            #---------------------------------------------------   
            elif msg_code==200:  #  Bootstrap Send clustering results and node joins to proper cluster
                #try: 

                    response = "Ack     200 "
                    print("Sending response 200")
                    server_socket.sendto(response.encode(), client_address)  # Send reply to detected client
                    anjem=message1.decode()
                    print()
#                   print(anjem)
                    handling_200(anjem)

                    
                #except Exception as e:
                #    print("❌ Error in handling message with code 100 : ",e)
                
            #---------------------------------------------------   
            elif msg_code==500:  # New node event and send the CH list to it
                response = "Ack     500 "
                print("Sending response 500")
                server_socket.sendto(response.encode(), client_address)  # Send reply to detected client
                message=""
                IP1=message1.decode()
                print(IP1)

                chlist=""  
                for i in range(len(node_IPs)):
                    if struct[i].role==2:
                        chlist=chlist+"<"+struct[i].node_name+","+struct[i].node_IP+","+struct[i].ch_port +">"
                chlist=chlist
                print(chlist) # CH list
                
                send_clustering_result("250", IP1, chlist)   

            #---------------------------------------------------   
                
            elif msg_code==250:  # Receives the CH list from Bootstrap and process it 
                # process message to exract ch list
                #serf serf code

                os.environ["PATH"] = "/opt/serfapp:" + os.environ["PATH"]
                global eth1_newnode, nearest_ch_name, nearest_ch_port, nearest_ch_ip
                
                response = "Ack     250 "
                print("Sending response 250")
                server_socket.sendto(response.encode(), client_address)  # Send Ack to the bootstrap
            
                
                print("received message",message1)
                data_str = message1.decode('utf-8')
                matches = re.findall(r'<(.*?)>', data_str)

                names = []
                ips = []
                ports = []

                for match in matches:
                    parts = match.split(',')
                    if len(parts) == 3:
                        name, ip, port = parts
                        names.append(name)
                        ips.append(ip)
                        ports.append(port)

                print("Names:", names)
                print("IPs:", ips)
                print("Ports:", ports)
                
                min_rtt, nearest_ch, live_chs=ping2(ips)
                for i in range(len(names)):
                    if ips[i]==nearest_ch:
                        nearest_ch_name=names[i]
                        nearest_ch_port=ports[i]
                        nearest_ch_ip=nearest_ch
                print("Nearest CH data:")
                print(nearest_ch_name, nearest_ch_port, nearest_ch_ip)
                # eth1_newnode = get_ip_address('eth1')
                 #print("eth1 IP:", eth1_newnode)


                #in_rtt=0    
                if min_rtt<max_latency: # connect to the nearest
                    #@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
                    # Anjem code for joining new node
                    
                    #
                    result = subprocess.run(['ip', 'addr', 'show', 'dev', 'eth1'], capture_output=True, text=True)
                    output = result.stdout
                    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', output)
                    if match:
                        nodeIP = match.group(1)
                        print(f"IP Address: {nodeIP}")
                    else:
                        print("IP address not found for eth1.")
                    
                    print("\nNearest Ch in range is: ", nearest_ch," with RTT=",min_rtt)
                    os.environ["PATH"] = "/opt/serfapp:" + os.environ["PATH"]
                    #eth1_newnode = get_ip_address('eth1')
                    #print("eth1 IP:", eth1_newnode)
                    os.system(
                        f"nohup ./serf agent "
                        f"-bind={nodeIP}:{nearest_ch_port} "
                        f"-advertise={nodeIP}:{nearest_ch_port} "
                        f"-node={nearest_ch_name}-member "
                        f"> serf_{nearest_ch_port}.log 2>&1 &"
                    )
                    print(f"Node started on {nodeIP}:{nearest_ch_port}")


                    # Wait a moment for the agent to start
                    time.sleep(3)

                 # Join the nearest CH
                    subprocess.run(
                        ["./serf", "join", f"{nearest_ch_ip}:{nearest_ch_port}"],
                    check=True
                    )
                    print(f"Member joined cluster via {nearest_ch_ip}:{nearest_ch_port}")

                    #end serf code
                    #@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@                  

                else:
                    try:
                        global CH_list_received_from_other_chs
                        print("\nDeceiding to be CH, becuse all CHs are out of",max_latency ,"ms range\n")
                        if len(live_chs)==0:
                            print("No CH is available to contact")
                        else:
                            print(len(live_chs),"neighboring CHs are available")
                            print(live_chs)
                            #CH_list_received_from_other_chs=[]
                            for i in range(len(live_chs)):
                                try:
                                    msg = "400"
                                    k=i+1
                                    print(f"\n{k}) Intoroducing this new node to the CH:",live_chs[i],"...")
                                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                    client_socket.settimeout(my_socket_timeout) # it raises an exception in 8 seconds
                                    client_socket.sendto(msg.encode(), (live_chs[i], 6000))  # <==== set to 5000
                                    response, _ = client_socket.recvfrom(1024)
                                    #print("Received CH List:",response.decode("utf-8"))
                                    #----------  receiving 450
                                    msgID, mem=from_json(response)
                                    if msgID==480: 
                                        print(f"\nContact was successfull, ❌ but destination node {live_chs[i]} is not a CH, msgID 480 is received")    
                                    elif msgID==450:
                                        CH_list_received_from_other_chs=CH_list_received_from_other_chs+mem

                                        print("Response=",msgID, "\nReceived CHs list:",mem)
                                    print("---------------------------------------------------------------")   

                                except socket.timeout:   
                                    print("❌ Node", live_chs[i],"does not respond ")
                                except Exception as e:
                                    print("❌ Error contacting ",e)
                                finally:
                                    client_socket.close()
                            print("Contacting CHs for introducing myself is over")
                            CH_list_received_from_other_chs=list(set(CH_list_received_from_other_chs)) # <<<< Remove duplicate members
                            print("final Ch list is :",CH_list_received_from_other_chs)  
                    except Exception as e:
                        print("Error contacting ",e)
     
                  
        #---------------------------------------------------         
            elif msg_code==400:   # I'm new the CH
                try:
                    if node_status!=2:  # Error message
                        print(f"Node isn't a CH, but it received a CH list request")
                        response = "Node isn't a CH, but it received a CH list request form"
                        print("An error message with code 480 sent back")
                    
                        msgID=480 # error message
                        response=to_json(msgID)   
                        server_socket.sendto(response.encode(), client_address)  # Send reply to detected client
                        continue


                    msgID=450
                    ch_list = ["172.220.0.10", "172.22.229.172", "172.22.228.3", "172.22.229.172", "172.22.228.100", "172.22.229.172", "172.22.229.172"]
                    response=to_json(msgID, ch_list) 
                    server_socket.sendto(response.encode(), client_address)  # Send reply to detected client
                    print('The list of CHs are sent to the node:', client_address) 

                except Exception as e:
                    print("❌ Error in handling message with code 400 : ",e)
        #---------------------------------------------------
            else: 
                response = "Unrecognised message "+str(msg_code)
                server_socket.sendto(response.encode(), client_address)  # Send reply to detected client

#    except Exception as e:
#        current_function = inspect.currentframe().f_code.co_name
#        line_number = inspect.currentframe().f_lineno        
#        print(f"❌ Error in Function ({current_function}) in line:{line_number}, >>>>>  Error message: {e}")
#        print("Automatic recovery... ")
#-----------------------------------------------------------------------------------------------------   

if __name__ == "__main__":
    
    #===================   Loading Global variables
    x=load_setup_data()
    node_port=int(x[0])
    max_latency=int(x[1]) 
    join_retry=int(x[2])    # when CH is not accessible, the client sleeps for the time specified by this variable and retires
    delay_between_join_retry=float(x[3])  # seconds
    my_socket_timeout=float(x[4])  # seconds
    ping_retries=int(x[5])
    
    print("\nLoading data from setup.txt ...")
    print(f"node_port = {node_port}")
    print(f"max_latency = {max_latency}")
    print(f"join_retry = {join_retry}")
    print(f"delay_between_join_retry = {delay_between_join_retry}")
    print(f"socket_timeout = {my_socket_timeout}")
    print(f"Ping retries = {ping_retries}\n")


    result = subprocess.run(['ip', 'addr', 'show', 'dev', 'eth1'], capture_output=True, text=True)
    output = result.stdout
    match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', output)
    if match:
        nodeIP = match.group(1)
        print(f"IP Address: {nodeIP}")
    else:
        print("IP address not found for eth1.")
 
    result=subprocess.run(['hostname'], capture_output=True, text=True)
    hname= result.stdout
    print("==Serf host name is : ", hname)
    hname=hname.strip()

    print("\nLocal IP address is:",nodeIP, "Host name=", hname)

start_server(nodeIP, node_port,hname)
