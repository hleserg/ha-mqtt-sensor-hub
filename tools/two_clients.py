import socket, time

HOST, PORT = "192.168.1.93", 5000

def probe(sock, label):
    """Is this socket still usable? Returns 'alive', 'closed', or an error."""
    sock.settimeout(2.0)
    try:
        data = sock.recv(1)
        return "closed by peer (EOF)" if data == b"" else "alive (data: %r)" % data
    except socket.timeout:
        return "alive (idle, no EOF)"
    except (ConnectionResetError, OSError) as e:
        return "closed (%s)" % type(e).__name__

a = socket.create_connection((HOST, PORT), timeout=10)
print("A connected from", a.getsockname())
time.sleep(2)
print("A before B:", probe(a, "A"))

b = socket.create_connection((HOST, PORT), timeout=10)
print("B connected from", b.getsockname())
time.sleep(3)

print("A after  B:", probe(a, "A"))
print("B after  B:", probe(b, "B"))

a.close(); b.close()
