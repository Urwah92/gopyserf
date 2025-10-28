import socket

def start_client(server_ip, server_port):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    while True:
        try:
            message = input("You: ")
            #if int(message)==100:
            #    message=["172.22.228.10", "172.22.228.2", "172.22.228.3", "172.22.228.4", "172.22.229.172"]
            client_socket.sendto(message.encode(), (server_ip, server_port))  # Send to server

            # Try to receive response (handle server disconnection)
         #   client_socket.settimeout(40)  # Set timeout to avoid hanging
            try:
                response, _ = client_socket.recvfrom(1024)
                print(f"Server: {response.decode()}")
            except socket.timeout:
                print("No response from server. It may be offline.")

        except ConnectionResetError:
            print("Error: Connection was forcibly closed by the server.")
            break
        except Exception as e:
            print(f"Unexpected error: {e}")
            break

if __name__ == "__main__":
    hostname = socket.gethostname()
    nodeIP = socket.gethostbyname(hostname)

    server_ip = input("Enter server IP address: ")  # Example: 127.0.0.1
    #server_ip = nodeIP
    z=input('Enter port number:\nPress enter for port 5000\n')
    if len(z)==0:
       port1=5000
    else:
        port1=int(z)   
    server_port=port1
    start_client(server_ip, server_port)
