import os
import xml.etree.ElementTree as ET

class EXParser():
    def __init__(self, filepath=''):
        self.filepath = filepath
        self.root = ET.parse(filepath).getroot() if os.path.exists(filepath) else ET.Element('Settings')
        
    def addSetting(self, id, town, agentmode):
        setting = ET.SubElement(self.root,'Setting',{'id':str(id),'town':str(town),'Agentmode':str(agentmode)})
        ET.SubElement(setting,'Vehicles')
        return setting
    
    def addWeather(self, setting, weather):
        weather_dict = {}
        for pattern in weather.__str__().split('(')[1].split(')')[0].split(','):
            key, value = pattern.strip().split('=')
            if key not in weather_dict:
                weather_dict[key] = value

        return ET.SubElement(setting,'weather',weather_dict)
    
    
    def addVehicle(self, setting, id, vtype, waypoints):
        print()
        vehicle =  ET.SubElement(setting.find('Vehicles'),'Vehicle',{'id':id,'type':vtype})
        print(vehicle)
        for waypoint in waypoints:
            ET.SubElement(vehicle,'waypoint',{'x':str(waypoint.x),'y':str(waypoint.y),'z':str(waypoint.z)})

    def update(self):
        writer = open(self.filepath,'w')
        writer.write(ET.tostring(self.root).decode())
        writer.close()
        self.root = ET.parse(self.filepath).getroot()