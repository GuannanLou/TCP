import carla


def ego_vehicle_parser(trajectory, ego_vehicle_vec, current_map, offset_range = 50):
    # start_location, end_location = trajectory
    result = []
    for i, location in enumerate(trajectory):

        waypoint = current_map.get_waypoint(location)
        offset = ego_vehicle_vec[i]
        start_direction = waypoint.transform.rotation.yaw % 360

        # print(waypoint.transform.location)
        # print('direction :', start_direction, waypoint.transform.rotation.yaw)
        # print('offset_vec:', offset)
        # print('offset    :', offset*offset_range - offset_range/2)

        if start_direction > 315 or start_direction < 45:
            new_location = carla.Location(x = location.x + offset*offset_range - offset_range/2,
                                          y = location.y,
                                          z = location.z)
        elif start_direction > 225:
            new_location = carla.Location(x = location.x,
                                          y = location.y - offset*offset_range + offset_range/2,
                                          z = location.z)
        elif start_direction > 135:
            new_location = carla.Location(x = location.x - offset*offset_range + offset_range/2,
                                          y = location.y,
                                          z = location.z)
        elif start_direction > 45: # Current road is in this direction. It is right.
            new_location = carla.Location(x = location.x,
                                          y = location.y + offset*offset_range - offset_range/2,
                                          z = location.z)
        # print('Old:', location)
        # print('New:', new_location)
        # print()
        result.append(new_location)

    return result


def other_vehicle_parser(other_vehicle_vec):
    result = [] 
    for flag in other_vehicle_vec:
        if flag >= 0.5:
            result.append(True)
        else:
            result.append(False)
        
    return result



def weather_parser(weather_vec):
    '''
    Converts a 9-length array of 0-1 numbers to a CARLA weather parameter object.

    Args:
        weather_vec (list): a 9-length array of 0-1 numbers
    
    Returns:
        carla.WeatherParameters
    '''
    c, p, pd, wi, sz, sl, fd, w, ff = weather_vec 
    return carla.WeatherParameters(
        cloudiness              = 0 if c  < 0.5 else (c -0.5)*2*100, 
        precipitation           = 0 if p  < 0.5 else (p -0.5)*2*100, 
        precipitation_deposits  = 0 if pd < 0.5 else (pd-0.5)*2*100, 
        wind_intensity          = 0 if wi < 0.5 else (wi-0.5)*2*100, 
        sun_azimuth_angle       = 0 if sz < 0.5 else (sz-0.5)*2*360, 
        sun_altitude_angle      = 0 if sl < 0.5 else (sl-0.5)*2*180-90, 
        fog_density             = 0 if fd < 0.5 else (fd-0.5)*2*100, 
        # fog_density             = 80, 
        wetness                 = 0 if w  < 0.5 else (w -0.5)*2*100, 
        fog_falloff             = 0 if ff < 0.5 else (ff-0.5)*2*5
    )