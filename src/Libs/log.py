
import time 
import os
import os.path

from datetime import datetime


log_file  = f"logs/sol_log_{datetime.utcfromtimestamp(int(time.time())).strftime('%Y%m%d_%H%M%S')}.log"

def log_main (log_file, msg):
    now = time.time()
    now_str = datetime.utcfromtimestamp(int(now)).strftime('%Y-%m-%d %H:%M:%S')
    msg = f"{now_str}:: TelegramBot :: {str(msg)}"
    print(msg)
    if log_file:
        try:
            with open(log_file, 'a+') as f:
                f.write(f'{msg}\n')
        except Exception as e:
            pass

def _log(msg):
    log_main (log_file, msg)


def log(msg):
    _log(msg)