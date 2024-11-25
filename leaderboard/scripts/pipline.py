import subprocess
import time
import csv
import datetime


current_datetime = datetime.datetime.now()
formatted_datetime = current_datetime.strftime("%Y-%m-%d|%H:%M:%S")
with open('{}.csv'.format('processLog|'+formatted_datetime), mode='w', newline='') as log_file:
    writer = csv.writer(log_file)
    writer.writerow(['time', 'ADS', 'section', 'level'])
    for ADS in ['TCP', 'InterFuser']:
        for section in ['Curve', 'Straight']:
            for level in [0,1,2,3,4]:

                current_time = datetime.datetime.now()
                writer.writerow([current_time, ADS, section, level])
                command = 'cd ~/Projects/carla;./CarlaUE4.sh --world-port=2000'
                try:
                    subprocess.Popen(command, shell=True)
                    # subprocess.run(command, shell=True, check=True)
                    print("Run carla")
                except subprocess.CalledProcessError as e:
                    print(f"Run carla fail: {e.returncode}")

                time.sleep(10)

                command = 'cd ~/Projects/TCP-Interfuser;sh leaderboard/scripts/test_basement.sh {} {} {}'.format(ADS, section, level)
                try:
                    subprocess.run(command, shell=True)
                    print("Run ADS")
                except subprocess.CalledProcessError as e:
                    print(f"Run ADS fail: {e.returncode}")