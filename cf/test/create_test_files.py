import datetime
import faulthandler
import os
import unittest

import numpy as np

faulthandler.enable()  # to debug seg faults and timeouts

import netCDF4

import cf

VN = cf.CF()


def _make_contiguous_file(filename):
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"

    n.createDimension("station", 4)
    n.createDimension("obs", 24)
    n.createDimension("name_strlen", 8)
    n.createDimension("bounds", 2)

    lon = n.createVariable("lon", "f8", ("station",))
    lon.standard_name = "longitude"
    lon.long_name = "station longitude"
    lon.units = "degrees_east"
    lon.bounds = "lon_bounds"
    lon[...] = [-23, 0, 67, 178]

    lon_bounds = n.createVariable("lon_bounds", "f8", ("station", "bounds"))
    lon_bounds[...] = [[-24, -22], [-1, 1], [66, 68], [177, 179]]

    lat = n.createVariable("lat", "f8", ("station",))
    lat.standard_name = "latitude"
    lat.long_name = "station latitude"
    lat.units = "degrees_north"
    lat[...] = [-9, 2, 34, 78]

    alt = n.createVariable("alt", "f8", ("station",))
    alt.long_name = "vertical distance above the surface"
    alt.standard_name = "height"
    alt.units = "m"
    alt.positive = "up"
    alt.axis = "Z"
    alt[...] = [0.5, 12.6, 23.7, 345]

    station_name = n.createVariable(
        "station_name", "S1", ("station", "name_strlen")
    )
    station_name.long_name = "station name"
    station_name.cf_role = "timeseries_id"
    station_name[...] = np.array(
        [
            [x for x in "station1"],
            [x for x in "station2"],
            [x for x in "station3"],
            [x for x in "station4"],
        ]
    )

    station_info = n.createVariable("station_info", "i4", ("station",))
    station_info.long_name = "some kind of station info"
    station_info[...] = [-10, -9, -8, -7]

    row_size = n.createVariable("row_size", "i4", ("station",))
    row_size.long_name = "number of observations for this station"
    row_size.sample_dimension = "obs"
    row_size[...] = [3, 7, 5, 9]

    time = n.createVariable("time", "f8", ("obs",))
    time.standard_name = "time"
    time.long_name = "time of measurement"
    time.units = "days since 1970-01-01 00:00:00"
    time.bounds = "time_bounds"
    time[0:3] = [-3, -2, -1]
    time[3:10] = [1, 2, 3, 4, 5, 6, 7]
    time[10:15] = [0.5, 1.5, 2.5, 3.5, 4.5]
    time[15:24] = range(-2, 7)

    time_bounds = n.createVariable("time_bounds", "f8", ("obs", "bounds"))
    time_bounds[..., 0] = time[...] - 0.5
    time_bounds[..., 1] = time[...] + 0.5

    humidity = n.createVariable("humidity", "f8", ("obs",), fill_value=-999.9)
    humidity.standard_name = "specific_humidity"
    humidity.coordinates = "time lat lon alt station_name station_info"
    humidity[0:3] = np.arange(0, 3)
    humidity[3:10] = np.arange(1, 71, 10)
    humidity[10:15] = np.arange(2, 502, 100)
    humidity[15:24] = np.arange(3, 9003, 1000)

    temp = n.createVariable("temp", "f8", ("obs",), fill_value=-999.9)
    temp.standard_name = "air_temperature"
    temp.units = "Celsius"
    temp.coordinates = "time lat lon alt station_name station_info"
    temp[...] = humidity[...] + 273.15

    n.close()

    return filename


def _make_indexed_file(filename):
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"

    n.createDimension("station", 4)
    n.createDimension("obs", None)
    n.createDimension("name_strlen", 8)
    n.createDimension("bounds", 2)

    lon = n.createVariable("lon", "f8", ("station",))
    lon.standard_name = "longitude"
    lon.long_name = "station longitude"
    lon.units = "degrees_east"
    lon.bounds = "lon_bounds"
    lon[...] = [-23, 0, 67, 178]

    lon_bounds = n.createVariable("lon_bounds", "f8", ("station", "bounds"))
    lon_bounds[...] = [[-24, -22], [-1, 1], [66, 68], [177, 179]]

    lat = n.createVariable("lat", "f8", ("station",))
    lat.standard_name = "latitude"
    lat.long_name = "station latitude"
    lat.units = "degrees_north"
    lat[...] = [-9, 2, 34, 78]

    alt = n.createVariable("alt", "f8", ("station",))
    alt.long_name = "vertical distance above the surface"
    alt.standard_name = "height"
    alt.units = "m"
    alt.positive = "up"
    alt.axis = "Z"
    alt[...] = [0.5, 12.6, 23.7, 345]

    station_name = n.createVariable(
        "station_name", "S1", ("station", "name_strlen")
    )
    station_name.long_name = "station name"
    station_name.cf_role = "timeseries_id"
    station_name[...] = np.array(
        [
            [x for x in "station1"],
            [x for x in "station2"],
            [x for x in "station3"],
            [x for x in "station4"],
        ]
    )

    station_info = n.createVariable("station_info", "i4", ("station",))
    station_info.long_name = "some kind of station info"
    station_info[...] = [-10, -9, -8, -7]

    # row_size[...] = [3, 7, 5, 9]
    stationIndex = n.createVariable("stationIndex", "i4", ("obs",))
    stationIndex.long_name = "which station this obs is for"
    stationIndex.instance_dimension = "station"
    stationIndex[...] = [
        3,
        2,
        1,
        0,
        2,
        3,
        3,
        3,
        1,
        1,
        0,
        2,
        3,
        1,
        0,
        1,
        2,
        3,
        2,
        3,
        3,
        3,
        1,
        1,
    ]

    t = [
        [-3, -2, -1],
        [1, 2, 3, 4, 5, 6, 7],
        [0.5, 1.5, 2.5, 3.5, 4.5],
        range(-2, 7),
    ]

    time = n.createVariable("time", "f8", ("obs",))
    time.standard_name = "time"
    time.long_name = "time of measurement"
    time.units = "days since 1970-01-01 00:00:00"
    time.bounds = "time_bounds"
    ssi = [0, 0, 0, 0]
    for i, si in enumerate(stationIndex[...]):
        time[i] = t[si][ssi[si]]
        ssi[si] += 1

    time_bounds = n.createVariable("time_bounds", "f8", ("obs", "bounds"))
    time_bounds[..., 0] = time[...] - 0.5
    time_bounds[..., 1] = time[...] + 0.5

    humidity = n.createVariable("humidity", "f8", ("obs",), fill_value=-999.9)
    humidity.standard_name = "specific_humidity"
    humidity.coordinates = "time lat lon alt station_name station_info"

    h = [
        np.arange(0, 3),
        np.arange(1, 71, 10),
        np.arange(2, 502, 100),
        np.arange(3, 9003, 1000),
    ]

    ssi = [0, 0, 0, 0]
    for i, si in enumerate(stationIndex[...]):
        humidity[i] = h[si][ssi[si]]
        ssi[si] += 1

    temp = n.createVariable("temp", "f8", ("obs",), fill_value=-999.9)
    temp.standard_name = "air_temperature"
    temp.units = "Celsius"
    temp.coordinates = "time lat lon alt station_name station_info"
    temp[...] = humidity[...] + 273.15

    n.close()

    return filename


def _make_indexed_contiguous_file(filename):
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeriesProfile"

    # 3 stations
    n.createDimension("station", 3)
    # 58 profiles spreadover 4 stations, each at a different time
    profile = n.createDimension("profile", 58)
    n.createDimension("obs", None)
    n.createDimension("name_strlen", 8)
    n.createDimension("bounds", 2)

    lon = n.createVariable("lon", "f8", ("station",))
    lon.standard_name = "longitude"
    lon.long_name = "station longitude"
    lon.units = "degrees_east"
    lon.bounds = "lon_bounds"
    lon[...] = [-23, 0, 67]

    lon_bounds = n.createVariable("lon_bounds", "f8", ("station", "bounds"))
    lon_bounds[...] = [[-24, -22], [-1, 1], [66, 68]]

    lat = n.createVariable("lat", "f8", ("station",))
    lat.standard_name = "latitude"
    lat.long_name = "station latitude"
    lat.units = "degrees_north"
    lat[...] = [-9, 2, 34]

    alt = n.createVariable("alt", "f8", ("station",))
    alt.long_name = "vertical distance above the surface"
    alt.standard_name = "height"
    alt.units = "m"
    alt.positive = "up"
    alt.axis = "Z"
    alt[...] = [0.5, 12.6, 23.7]

    station_name = n.createVariable(
        "station_name", "S1", ("station", "name_strlen")
    )
    station_name.long_name = "station name"
    station_name.cf_role = "timeseries_id"
    station_name[...] = np.array(
        [
            [x for x in "station1"],
            [x for x in "station2"],
            [x for x in "station3"],
        ]
    )

    profile = n.createVariable("profile", "i4", ("profile"))
    profile.cf_role = "profile_id"
    profile[...] = np.arange(58) + 100

    station_info = n.createVariable("station_info", "i4", ("station",))
    station_info.long_name = "some kind of station info"
    station_info[...] = [-10, -9, -8]

    stationIndex = n.createVariable("stationIndex", "i4", ("profile",))
    stationIndex.long_name = "which station this profile is for"
    stationIndex.instance_dimension = "station"
    stationIndex[...] = [
        2,
        1,
        0,
        2,
        1,
        1,
        0,
        2,
        1,
        0,
        1,
        2,
        2,
        1,
        1,
        2,
        1,
        0,
        2,
        1,
        1,
        0,
        2,
        1,
        0,
        1,
        2,
        2,
        1,
        1,
        2,
        1,
        0,
        2,
        1,
        1,
        0,
        2,
        1,
        0,
        1,
        2,
        2,
        1,
        1,
        2,
        1,
        0,
        2,
        1,
        1,
        0,
        2,
        1,
        0,
        1,
        2,
        2,
    ]
    # station N has list(stationIndex[...]).count(N) profiles

    row_size = n.createVariable("row_size", "i4", ("profile",))
    row_size.long_name = "number of observations for this profile"
    row_size.sample_dimension = "obs"
    row_size[...] = [
        1,
        4,
        1,
        3,
        2,
        2,
        3,
        3,
        1,
        2,
        2,
        3,
        2,
        2,
        2,
        2,
        1,
        2,
        1,
        3,
        3,
        2,
        1,
        3,
        1,
        3,
        2,
        3,
        1,
        3,
        3,
        2,
        2,
        2,
        1,
        1,
        1,
        3,
        1,
        1,
        2,
        1,
        1,
        3,
        3,
        2,
        2,
        2,
        2,
        1,
        2,
        3,
        3,
        3,
        2,
        3,
        1,
        1,
    ]  # sum = 118

    time = n.createVariable("time", "f8", ("profile",))
    time.standard_name = "time"
    time.long_name = "time"
    time.units = "days since 1970-01-01 00:00:00"
    time.bounds = "time_bounds"
    t0 = [3, 0, -3]
    ssi = [0, 0, 0]
    for i, si in enumerate(stationIndex[...]):
        time[i] = t0[si] + ssi[si]
        ssi[si] += 1

    time_bounds = n.createVariable("time_bounds", "f8", ("profile", "bounds"))
    time_bounds[..., 0] = time[...] - 0.5
    time_bounds[..., 1] = time[...] + 0.5

    z = n.createVariable("z", "f8", ("obs",))
    z.standard_name = "altitude"
    z.long_name = "height above mean sea level"
    z.units = "km"
    z.axis = "Z"
    z.positive = "up"
    z.bounds = "z_bounds"

    #        z0 = [1, 0, 3]
    #        i = 0
    #        for s, r in zip(stationIndex[...], row_size[...]):
    #            z[i:i+r] = z0[s] + np.sort(
    #                np.random.uniform(0, np.random.uniform(1, 2), r))
    #            i += r

    data = [
        3.51977705293769,
        0.521185292100177,
        0.575154265863394,
        1.08495843717095,
        1.37710968624395,
        2.07123455611723,
        3.47064474274781,
        3.88569849023813,
        4.81069254279537,
        0.264339600625496,
        0.915704970094182,
        0.0701532210336895,
        0.395517651420933,
        1.00657582854276,
        1.17721374303641,
        1.82189345615046,
        3.52424307197668,
        3.93200473199559,
        3.95715099603671,
        1.57047493027102,
        1.09938982652955,
        1.17768722826975,
        0.251803399458277,
        1.59673486865804,
        4.02868944763605,
        4.03749228832264,
        4.79858281590985,
        3.00019933315412,
        3.65124061660449,
        0.458463542157766,
        0.978678197083262,
        0.0561560792556281,
        0.31182013232255,
        3.33350065357286,
        4.33143904011861,
        0.377894196412131,
        1.63020681064712,
        2.00097025264771,
        3.76948048424458,
        0.572927165845568,
        1.29408313557905,
        1.81296270533192,
        0.387142669131077,
        0.693459187515738,
        1.69261930636298,
        1.38258797228361,
        1.82590759889566,
        3.34993297710761,
        0.725250730922501,
        1.38221693486728,
        1.59828555215646,
        1.59281225554253,
        0.452340646918555,
        0.976663373825433,
        1.12640496317618,
        3.19366847375422,
        3.37209133117904,
        3.40665008236976,
        3.53525896684001,
        4.10444186715724,
        0.14920937817654,
        0.0907197953552753,
        0.42527916794473,
        0.618685137936187,
        3.01900591447357,
        3.37205542289986,
        3.86957342976163,
        0.17175098751914,
        0.990040375014957,
        1.57011428605984,
        2.12140567043994,
        3.24374743730506,
        4.24042441581785,
        0.929509749153725,
        0.0711997786817564,
        2.25090028461898,
        3.31520955860746,
        3.49482624434274,
        3.96812568493549,
        1.5681807261767,
        1.79993011515465,
        0.068325990211909,
        0.124469638352167,
        3.31990436971169,
        3.84766748039389,
        0.451973490541035,
        1.24303219956085,
        1.30478004656262,
        0.351892459787624,
        0.683685812990457,
        0.788883736575568,
        3.73033428872491,
        3.99479807507392,
        0.811582011950481,
        1.2241242448019,
        1.25563109687369,
        2.16603674712822,
        3.00010622131408,
        3.90637137662453,
        0.589586644805982,
        0.104656387266266,
        0.961185900148304,
        1.05120351477824,
        1.29460917520233,
        2.10139985693684,
        3.64252693587415,
        3.91197236350995,
        4.56466622863717,
        0.556476687600461,
        0.783717448678148,
        0.910917550635007,
        1.59750076220451,
        1.97101264162631,
        0.714693043642084,
        0.904381625638779,
        1.03767817888021,
        4.10124675852254,
        3.1059214185543,
    ]
    data = np.around(data, 2)
    z[...] = data

    z_bounds = n.createVariable("z_bounds", "f8", ("obs", "bounds"))
    z_bounds[..., 0] = z[...] - 0.01
    z_bounds[..., 1] = z[...] + 0.01

    humidity = n.createVariable("humidity", "f8", ("obs",), fill_value=-999.9)
    humidity.standard_name = "specific_humidity"
    humidity.coordinates = (
        "time lat lon alt z station_name station_info profile"
    )

    data *= 10
    data = np.around(data, 2)
    humidity[...] = data

    temp = n.createVariable("temp", "f8", ("obs",), fill_value=-999.9)
    temp.standard_name = "air_temperature"
    temp.units = "Celsius"
    temp.coordinates = "time lat lon alt z station_name station_info profile"

    data += 2731.5
    data = np.around(data, 2)
    temp[...] = data

    n.close()

    return filename


def _make_external_files():
    """Make netCDF files with external variables."""

    def _pp(
        filename,
        parent=False,
        external=False,
        combined=False,
        external_missing=False,
    ):
        """Make a netCDF file with some external variables."""
        nc = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

        nc.createDimension("grid_latitude", 10)
        nc.createDimension("grid_longitude", 9)

        nc.Conventions = "CF-" + VN
        if parent:
            nc.external_variables = "areacella"

        if parent or combined or external_missing:
            grid_latitude = nc.createVariable(
                dimensions=("grid_latitude",),
                datatype="f8",
                varname="grid_latitude",
            )
            grid_latitude.setncatts(
                {"units": "degrees", "standard_name": "grid_latitude"}
            )
            grid_latitude[...] = range(10)

            grid_longitude = nc.createVariable(
                dimensions=("grid_longitude",),
                datatype="f8",
                varname="grid_longitude",
            )
            grid_longitude.setncatts(
                {"units": "degrees", "standard_name": "grid_longitude"}
            )
            grid_longitude[...] = range(9)

            latitude = nc.createVariable(
                dimensions=("grid_latitude", "grid_longitude"),
                datatype="i4",
                varname="latitude",
            )
            latitude.setncatts(
                {"units": "degree_N", "standard_name": "latitude"}
            )

            latitude[...] = np.arange(90).reshape(10, 9)

            longitude = nc.createVariable(
                dimensions=("grid_longitude", "grid_latitude"),
                datatype="i4",
                varname="longitude",
            )
            longitude.setncatts(
                {"units": "degreeE", "standard_name": "longitude"}
            )
            longitude[...] = np.arange(90).reshape(9, 10)

            eastward_wind = nc.createVariable(
                dimensions=("grid_latitude", "grid_longitude"),
                datatype="f8",
                varname="eastward_wind",
            )
            eastward_wind.coordinates = "latitude longitude"
            eastward_wind.standard_name = "eastward_wind"
            eastward_wind.cell_methods = (
                "grid_longitude: mean (interval: 1 day comment: ok) "
                "grid_latitude: maximum where sea"
            )
            eastward_wind.cell_measures = "area: areacella"
            eastward_wind.units = "m s-1"
            eastward_wind[...] = np.arange(90).reshape(10, 9) - 45.5

        if external or combined:
            areacella = nc.createVariable(
                dimensions=("grid_longitude", "grid_latitude"),
                datatype="f8",
                varname="areacella",
            )
            areacella.setncatts({"units": "m2", "standard_name": "cell_area"})
            areacella[...] = np.arange(90).reshape(9, 10) + 100000.5

        nc.close()

    parent_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "parent.nc"
    )
    external_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "external.nc"
    )
    combined_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "combined.nc"
    )
    external_missing_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "external_missing.nc"
    )

    _pp(parent_file, parent=True)
    _pp(external_file, external=True)
    _pp(combined_file, combined=True)
    _pp(external_missing_file, external_missing=True)

    return parent_file, external_file, combined_file, external_missing_file


def _make_gathered_file(filename):
    """Make a netCDF file with a gathered array."""

    def _jj(shape, list_values):
        array = np.ma.masked_all(shape)
        for i, (index, x) in enumerate(np.ndenumerate(array)):
            if i in list_values:
                array[index] = i
        return array

    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN

    time = n.createDimension("time", 2)
    height = n.createDimension("height", 3)
    lat = n.createDimension("lat", 4)
    lon = n.createDimension("lon", 5)
    p = n.createDimension("p", 6)

    list1 = n.createDimension("list1", 4)
    list2 = n.createDimension("list2", 9)
    list3 = n.createDimension("list3", 14)

    # Dimension coordinate variables
    time = n.createVariable("time", "f8", ("time",))
    time.standard_name = "time"
    time.units = "days since 2000-1-1"
    time[...] = [31, 60]

    height = n.createVariable("height", "f8", ("height",))
    height.standard_name = "height"
    height.units = "metres"
    height.positive = "up"
    height[...] = [0.5, 1.5, 2.5]

    lat = n.createVariable("lat", "f8", ("lat",))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat[...] = [-90, -85, -80, -75]

    p = n.createVariable("p", "i4", ("p",))
    p.long_name = "pseudolevel"
    p[...] = [1, 2, 3, 4, 5, 6]

    # Auxiliary coordinate variables

    aux0 = n.createVariable("aux0", "f8", ("list1",))
    aux0.standard_name = "longitude"
    aux0.units = "degrees_east"
    aux0[...] = np.arange(list1.size)

    aux1 = n.createVariable("aux1", "f8", ("list3",))
    aux1[...] = np.arange(list3.size)

    aux2 = n.createVariable("aux2", "f8", ("time", "list3", "p"))
    aux2[...] = np.arange(time.size * list3.size * p.size).reshape(
        time.size, list3.size, p.size
    )

    aux3 = n.createVariable("aux3", "f8", ("p", "list3", "time"))
    aux3[...] = np.arange(p.size * list3.size * time.size).reshape(
        p.size, list3.size, time.size
    )

    aux4 = n.createVariable("aux4", "f8", ("p", "time", "list3"))
    aux4[...] = np.arange(p.size * time.size * list3.size).reshape(
        p.size, time.size, list3.size
    )

    aux5 = n.createVariable("aux5", "f8", ("list3", "p", "time"))
    aux5[...] = np.arange(list3.size * p.size * time.size).reshape(
        list3.size, p.size, time.size
    )

    aux6 = n.createVariable("aux6", "f8", ("list3", "time"))
    aux6[...] = np.arange(list3.size * time.size).reshape(
        list3.size, time.size
    )

    aux7 = n.createVariable("aux7", "f8", ("lat",))
    aux7[...] = np.arange(lat.size)

    aux8 = n.createVariable("aux8", "f8", ("lon", "lat"))
    aux8[...] = np.arange(lon.size * lat.size).reshape(lon.size, lat.size)

    aux9 = n.createVariable("aux9", "f8", ("time", "height"))
    aux9[...] = np.arange(time.size * height.size).reshape(
        time.size, height.size
    )

    # List variables
    list1 = n.createVariable("list1", "i", ("list1",))
    list1.compress = "lon"
    list1[...] = [0, 1, 3, 4]

    list2 = n.createVariable("list2", "i", ("list2",))
    list2.compress = "lat lon"
    list2[...] = [0, 1, 5, 6, 13, 14, 17, 18, 19]

    list3 = n.createVariable("list3", "i", ("list3",))
    list3.compress = "height lat lon"
    array = _jj(
        (3, 4, 5), [0, 1, 5, 6, 13, 14, 25, 26, 37, 38, 48, 49, 58, 59]
    )
    list3[...] = array.compressed()

    # Data variables
    temp1 = n.createVariable(
        "temp1", "f8", ("time", "height", "lat", "list1", "p")
    )
    temp1.long_name = "temp1"
    temp1.units = "K"
    temp1.coordinates = "aux0 aux7 aux8 aux9"
    temp1[...] = np.arange(2 * 3 * 4 * 4 * 6).reshape(2, 3, 4, 4, 6)

    temp2 = n.createVariable("temp2", "f8", ("time", "height", "list2", "p"))
    temp2.long_name = "temp2"
    temp2.units = "K"
    temp2.coordinates = "aux7 aux8 aux9"
    temp2[...] = np.arange(2 * 3 * 9 * 6).reshape(2, 3, 9, 6)

    temp3 = n.createVariable("temp3", "f8", ("time", "list3", "p"))
    temp3.long_name = "temp3"
    temp3.units = "K"
    temp3.coordinates = "aux0 aux1 aux2 aux3 aux4 aux5 aux6 aux7 aux8 aux9"
    temp3[...] = np.arange(2 * 14 * 6).reshape(2, 14, 6)

    n.close()

    return filename


# --------------------------------------------------------------------
# Geometry files
# --------------------------------------------------------------------
def _make_geometry_1_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = (
        "Make a netCDF file with 2 node coordinates variables, each of "
        "which has a corresponding auxiliary coordinate variable."
    )

    n.createDimension("time", 4)
    n.createDimension("instance", 2)
    n.createDimension("node", 5)

    t = n.createVariable("time", "i4", ("time",))
    t.units = "seconds since 2016-11-07 20:00 UTC"
    t[...] = [1, 2, 3, 4]

    lat = n.createVariable("lat", "f8", ("instance",))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat.nodes = "y"
    lat[...] = [30, 50]

    lon = n.createVariable("lon", "f8", ("instance",))
    lon.standard_name = "longitude"
    lon.units = "degrees_east"
    lon.nodes = "x"
    lon[...] = [10, 60]

    datum = n.createVariable("datum", "i4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.longitude_of_prime_meridian = 0.0
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "line"
    geometry_container.node_count = "node_count"
    geometry_container.node_coordinates = "x y"

    node_count = n.createVariable("node_count", "i4", ("instance",))
    node_count[...] = [3, 2]

    x = n.createVariable("x", "f8", ("node",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [30, 10, 40, 50, 50]

    y = n.createVariable("y", "f8", ("node",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [10, 30, 40, 60, 50]

    pr = n.createVariable("pr", "f8", ("instance", "time"))
    pr.standard_name = "precipitation_amount"
    pr.units = "kg m-2"
    pr.coordinates = "time lat lon"
    pr.grid_mapping = "datum"
    pr.geometry = "geometry_container"
    pr[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "time lat lon"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[10, 20, 30, 40], [50, 60, 70, 80]]

    n.close()

    return filename


def _make_geometry_2_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = (
        "A netCDF file with 3 node coordinates variables, only two of "
        "which have a corresponding auxiliary coordinate variable."
    )

    n.createDimension("time", 4)
    n.createDimension("instance", 2)
    n.createDimension("node", 5)

    t = n.createVariable("time", "i4", ("time",))
    t.units = "seconds since 2016-11-07 20:00 UTC"
    t[...] = [1, 2, 3, 4]

    lat = n.createVariable("lat", "f8", ("instance",))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat.nodes = "y"
    lat[...] = [30, 50]

    lon = n.createVariable("lon", "f8", ("instance",))
    lon.standard_name = "longitude"
    lon.units = "degrees_east"
    lon.nodes = "x"
    lon[...] = [10, 60]

    datum = n.createVariable("datum", "i4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.longitude_of_prime_meridian = 0.0
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "line"
    geometry_container.node_count = "node_count"
    geometry_container.node_coordinates = "x y z"

    node_count = n.createVariable("node_count", "i4", ("instance",))
    node_count[...] = [3, 2]

    x = n.createVariable("x", "f8", ("node",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [30, 10, 40, 50, 50]

    y = n.createVariable("y", "f8", ("node",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [10, 30, 40, 60, 50]

    z = n.createVariable("z", "f8", ("node",))
    z.units = "m"
    z.standard_name = "altitude"
    z.axis = "Z"
    z[...] = [100, 150, 200, 125, 80]

    someData = n.createVariable("someData", "f8", ("instance", "time"))
    someData.coordinates = "time lat lon"
    someData.grid_mapping = "datum"
    someData.geometry = "geometry_container"
    someData[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "time lat lon"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    n.close()

    return filename


def _make_geometry_3_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = (
        "A netCDF file with 3 node coordinates variables, each of which "
        "contains only one point, only two of which have a corresponding "
        "auxiliary coordinate variables. There is no node count variable."
    )

    n.createDimension("time", 4)
    n.createDimension("instance", 3)

    t = n.createVariable("time", "i4", ("time",))
    t.units = "seconds since 2016-11-07 20:00 UTC"
    t[...] = [1, 2, 3, 4]

    lat = n.createVariable("lat", "f8", ("instance",))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat.nodes = "y"
    lat[...] = [30, 50, 70]

    lon = n.createVariable("lon", "f8", ("instance",))
    lon.standard_name = "longitude"
    lon.units = "degrees_east"
    lon.nodes = "x"
    lon[...] = [10, 60, 80]

    datum = n.createVariable("datum", "i4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.longitude_of_prime_meridian = 0.0
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "point"
    geometry_container.node_coordinates = "x y z"

    x = n.createVariable("x", "f8", ("instance",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [30, 10, 40]

    y = n.createVariable("y", "f8", ("instance",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [10, 30, 40]

    z = n.createVariable("z", "f8", ("instance",))
    z.units = "m"
    z.standard_name = "altitude"
    z.axis = "Z"
    z[...] = [100, 150, 200]

    someData_1 = n.createVariable("someData_1", "f8", ("instance", "time"))
    someData_1.coordinates = "lat lon"
    someData_1.grid_mapping = "datum"
    someData_1.geometry = "geometry_container"
    someData_1[...] = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "lat lon"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[10, 20, 30, 40], [50, 60, 70, 80], [90, 100, 110, 120]]

    n.close()

    return filename


def _make_geometry_4_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = (
        "A netCDF file with 2 node coordinates variables, none of which "
        "have a corresponding auxiliary coordinate variable."
    )

    n.createDimension("time", 4)
    n.createDimension("instance", 2)
    n.createDimension("node", 5)
    n.createDimension("strlen", 2)

    # Variables
    t = n.createVariable("time", "i4", ("time",))
    t.standard_name = "time"
    t.units = "days since 2000-01-01"
    t[...] = [1, 2, 3, 4]

    instance_id = n.createVariable("instance_id", "S1", ("instance", "strlen"))
    instance_id.cf_role = "timeseries_id"
    instance_id[...] = [["x", "1"], ["y", "2"]]

    datum = n.createVariable("datum", "i4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.longitude_of_prime_meridian = 0.0
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "line"
    geometry_container.node_count = "node_count"
    geometry_container.node_coordinates = "x y"

    node_count = n.createVariable("node_count", "i4", ("instance",))
    node_count[...] = [3, 2]

    x = n.createVariable("x", "f8", ("node",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [30, 10, 40, 50, 50]

    y = n.createVariable("y", "f8", ("node",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [10, 30, 40, 60, 50]

    someData_1 = n.createVariable("someData_1", "f8", ("instance", "time"))
    someData_1.coordinates = "instance_id"
    someData_1.grid_mapping = "datum"
    someData_1.geometry = "geometry_container"
    someData_1[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "instance_id"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[10, 20, 30, 40], [50, 60, 70, 80]]

    n.close()

    return filename


def _make_interior_ring_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    # Global attributes
    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = "TODO"

    # Dimensions
    n.createDimension("time", 4)
    n.createDimension("instance", 2)
    n.createDimension("node", 13)
    n.createDimension("part", 4)
    n.createDimension("strlen", 2)

    # Variables
    t = n.createVariable("time", "i4", ("time",))
    t.standard_name = "time"
    t.units = "days since 2000-01-01"
    t[...] = [1, 2, 3, 4]

    instance_id = n.createVariable("instance_id", "S1", ("instance", "strlen"))
    instance_id.cf_role = "timeseries_id"
    instance_id[...] = [["x", "1"], ["y", "2"]]

    x = n.createVariable("x", "f8", ("node",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [20, 10, 0, 5, 10, 15, 10, 20, 10, 0, 50, 40, 30]

    y = n.createVariable("y", "f8", ("node",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [0, 15, 0, 5, 10, 5, 5, 20, 35, 20, 0, 15, 0]

    z = n.createVariable("z", "f8", ("instance",))
    z.units = "m"
    z.standard_name = "altitude"
    z.positive = "up"
    z.axis = "Z"
    z[...] = [5000, 20]

    lat = n.createVariable("lat", "f8", ("instance",))
    lat.units = "degrees_north"
    lat.standard_name = "latitude"
    lat.nodes = "y"
    lat[...] = [25, 7]

    lon = n.createVariable("lon", "f8", ("instance",))
    lon.units = "degrees_east"
    lon.standard_name = "longitude"
    lon.nodes = "x"
    lon[...] = [10, 40]

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "polygon"
    geometry_container.node_count = "node_count"
    geometry_container.node_coordinates = "x y"
    geometry_container.grid_mapping = "datum"
    geometry_container.coordinates = "lat lon"
    geometry_container.part_node_count = "part_node_count"
    geometry_container.interior_ring = "interior_ring"

    node_count = n.createVariable("node_count", "i4", ("instance"))
    node_count[...] = [10, 3]

    part_node_count = n.createVariable("part_node_count", "i4", ("part"))
    part_node_count[...] = [3, 4, 3, 3]

    interior_ring = n.createVariable("interior_ring", "i4", ("part"))
    interior_ring[...] = [0, 1, 0, 0]

    datum = n.createVariable("datum", "f4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563
    datum.longitude_of_prime_meridian = 0.0

    pr = n.createVariable("pr", "f8", ("instance", "time"))
    pr.standard_name = "preciptitation_amount"
    pr.standard_units = "kg m-2"
    pr.coordinates = "time lat lon z instance_id"
    pr.grid_mapping = "datum"
    pr.geometry = "geometry_container"
    pr[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "time lat lon z instance_id"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    n.close()

    return filename


def _make_interior_ring_file_2(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    # Global attributes
    n.Conventions = "CF-" + VN
    n.featureType = "timeSeries"
    n.comment = "TODO"

    # Dimensions
    n.createDimension("time", 4)
    n.createDimension("instance", 2)
    n.createDimension("node", 13)
    n.createDimension("part", 4)
    n.createDimension("strlen", 2)

    # Variables
    t = n.createVariable("time", "i4", ("time",))
    t.standard_name = "time"
    t.units = "days since 2000-01-01"
    t[...] = [1, 2, 3, 4]

    instance_id = n.createVariable("instance_id", "S1", ("instance", "strlen"))
    instance_id.cf_role = "timeseries_id"
    instance_id[...] = [["x", "1"], ["y", "2"]]

    x = n.createVariable("x", "f8", ("node",))
    x.units = "degrees_east"
    x.standard_name = "longitude"
    x.axis = "X"
    x[...] = [20, 10, 0, 5, 10, 15, 10, 20, 10, 0, 50, 40, 30]

    y = n.createVariable("y", "f8", ("node",))
    y.units = "degrees_north"
    y.standard_name = "latitude"
    y.axis = "Y"
    y[...] = [0, 15, 0, 5, 10, 5, 5, 20, 35, 20, 0, 15, 0]

    z = n.createVariable("z", "f8", ("node",))
    z.units = "m"
    z.standard_name = "altitude"
    z.axis = "Z"
    z[...] = [1, 2, 4, 2, 3, 4, 5, 5, 1, 4, 3, 2, 1]

    lat = n.createVariable("lat", "f8", ("instance",))
    lat.units = "degrees_north"
    lat.standard_name = "latitude"
    lat.nodes = "y"
    lat[...] = [25, 7]

    lon = n.createVariable("lon", "f8", ("instance",))
    lon.units = "degrees_east"
    lon.standard_name = "longitude"
    lon.nodes = "x"
    lon[...] = [10, 40]

    geometry_container = n.createVariable("geometry_container", "i4", ())
    geometry_container.geometry_type = "polygon"
    geometry_container.node_count = "node_count"
    geometry_container.node_coordinates = "x y z"
    geometry_container.grid_mapping = "datum"
    geometry_container.coordinates = "lat lon"
    geometry_container.part_node_count = "part_node_count"
    geometry_container.interior_ring = "interior_ring"

    node_count = n.createVariable("node_count", "i4", ("instance"))
    node_count[...] = [10, 3]

    part_node_count = n.createVariable("part_node_count", "i4", ("part"))
    part_node_count[...] = [3, 4, 3, 3]

    interior_ring = n.createVariable("interior_ring", "i4", ("part"))
    interior_ring[...] = [0, 1, 0, 0]

    datum = n.createVariable("datum", "f4", ())
    datum.grid_mapping_name = "latitude_longitude"
    datum.semi_major_axis = 6378137.0
    datum.inverse_flattening = 298.257223563
    datum.longitude_of_prime_meridian = 0.0

    pr = n.createVariable("pr", "f8", ("instance", "time"))
    pr.standard_name = "preciptitation_amount"
    pr.standard_units = "kg m-2"
    pr.coordinates = "time lat lon z instance_id"
    pr.grid_mapping = "datum"
    pr.geometry = "geometry_container"
    pr[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    someData_2 = n.createVariable("someData_2", "f8", ("instance", "time"))
    someData_2.coordinates = "time lat lon z instance_id"
    someData_2.grid_mapping = "datum"
    someData_2.geometry = "geometry_container"
    someData_2[...] = [[1, 2, 3, 4], [5, 6, 7, 8]]

    n.close()

    return filename


def _make_string_char_file(filename):
    """See n.comment for details."""
    n = netCDF4.Dataset(filename, "w", format="NETCDF4")

    n.Conventions = "CF-" + VN
    n.comment = "A netCDF file with variables of string and char data types"

    n.createDimension("dim1", 1)
    n.createDimension("time", 4)
    n.createDimension("lat", 2)
    n.createDimension("lon", 3)
    n.createDimension("strlen8", 8)
    n.createDimension("strlen7", 7)
    n.createDimension("strlen5", 5)
    n.createDimension("strlen3", 3)

    months = np.array(["January", "February", "March", "April"], dtype="S8")

    months_m = np.ma.array(
        months, dtype="S7", mask=[0, 1, 0, 0], fill_value=b""
    )

    numbers = np.array(
        [["one", "two", "three"], ["four", "five", "six"]], dtype="S5"
    )

    s_months4 = n.createVariable("s_months4", str, ("time",))
    s_months4.long_name = "string: Four months"
    s_months4[:] = months

    s_months1 = n.createVariable("s_months1", str, ("dim1",))
    s_months1.long_name = "string: One month"
    s_months1[:] = np.array(["December"], dtype="S8")

    s_months0 = n.createVariable("s_months0", str, ())
    s_months0.long_name = "string: One month (scalar)"
    s_months0[:] = np.array(["May"], dtype="S3")

    s_numbers = n.createVariable("s_numbers", str, ("lat", "lon"))
    s_numbers.long_name = "string: Two dimensional"
    s_numbers[...] = numbers

    s_months4m = n.createVariable("s_months4m", str, ("time",))
    s_months4m.long_name = "string: Four months (masked)"
    array = months.copy()
    array[1] = ""
    s_months4m[...] = array

    c_months4 = n.createVariable("c_months4", "S1", ("time", "strlen8"))
    c_months4.long_name = "char: Four months"
    c_months4[:, :] = netCDF4.stringtochar(months)

    c_months1 = n.createVariable("c_months1", "S1", ("dim1", "strlen8"))
    c_months1.long_name = "char: One month"
    c_months1[:] = netCDF4.stringtochar(np.array(["December"], dtype="S8"))
    c_months0 = n.createVariable("c_months0", "S1", ("strlen3",))
    c_months0.long_name = "char: One month (scalar)"
    c_months0[:] = np.array(list("May"))

    c_numbers = n.createVariable("c_numbers", "S1", ("lat", "lon", "strlen5"))
    c_numbers.long_name = "char: Two dimensional"
    c_numbers[...] = netCDF4.stringtochar(numbers)

    c_months4m = n.createVariable("c_months4m", "S1", ("time", "strlen7"))
    c_months4m.long_name = "char: Four months (masked)"
    array = netCDF4.stringtochar(months_m)
    c_months4m[:, :] = array

    n.close()

    return filename


def _make_broken_bounds_cdl(filename):
    with open(filename, mode="w") as f:
        f.write(
            """netcdf broken_bounds {
dimensions:
      lat = 180 ;
      bnds = 2 ;
      lon = 288 ;
      time = UNLIMITED ; // (1825 currently)
variables:
      double lat(lat) ;
           lat:long_name = "latitude" ;
           lat:units = "degrees_north" ;
           lat:axis = "Y" ;
           lat:bounds = "lat_bnds" ;
           lat:standard_name = "latitude" ;
           lat:cell_methods = "time: point" ;
      double lat_bnds(lat, bnds) ;
           lat_bnds:long_name = "latitude bounds" ;
           lat_bnds:units = "degrees_north" ;
           lat_bnds:axis = "Y" ;
      double lon(lon) ;
           lon:long_name = "longitude" ;
           lon:units = "degrees_east" ;
           lon:axis = "X" ;
           lon:bounds = "lon_bnds" ;
           lon:standard_name = "longitude" ;
           lon:cell_methods = "time: point" ;
      double lon_bnds(lon, bnds) ;
           lon_bnds:long_name = "longitude bounds" ;
           lon_bnds:units = "m" ;
           lon_bnds:axis = "X" ;
      float pr(time, lat, lon) ;
           pr:long_name = "Precipitation" ;
           pr:units = "kg m-2 s-1" ;
           pr:missing_value = 1.e+20f ;
           pr:_FillValue = 1.e+20f ;
           pr:cell_methods = "area: time: mean" ;
           pr:cell_measures = "area: areacella" ;
           pr:standard_name = "precipitation_flux" ;
           pr:interp_method = "conserve_order1" ;
           pr:original_name = "pr" ;
      double time(time) ;
           time:long_name = "time" ;
           time:units = "days since 1850-01-01 00:00:00" ;
           time:axis = "T" ;
           time:calendar_type = "noleap" ;
           time:calendar = "noleap" ;
           time:bounds = "time_bnds" ;
           time:standard_name = "time" ;
           time:description = "Temporal mean" ;
      double time_bnds(time, bnds) ;
           time_bnds:long_name = "time axis boundaries" ;
           time_bnds:units = "days since 1850-01-01 00:00:00" ;

// global attributes:
           :external_variables = "areacella" ;
           :Conventions = "CF-"""
            + VN
            + """" ;
           :source = "model" ;
           :comment = "Bounds variable has incompatible units to its parent coordinate variable" ;
}
"""
        )


def _make_subsampled_1(filename):
    """Lossy compression by coordinate subsampling (1).

    Make a netCDF file with lossy compression by coordinate subsampling
    and reconstitution by linear, bilinear, and quadratic interpolation.

    """
    n = netCDF4.Dataset(filename, "w", format="NETCDF3_CLASSIC")

    n.Conventions = f"CF-{VN}"
    n.comment = (
        "A netCDF file with lossy compression by coordinate subsampling "
        "and reconstitution by linear, bilinear, and quadratic "
        "interpolation."
    )

    # Dimensions
    n.createDimension("time", 2)
    n.createDimension("lat", 18)
    n.createDimension("lon", 12)
    n.createDimension("tp_lat", 4)
    n.createDimension("tp_lon", 5)
    n.createDimension("subarea_lat", 2)
    n.createDimension("subarea_lon", 3)

    n.createDimension("bounds2", 2)
    n.createDimension("bounds4", 4)

    # Tie point index variables
    lat_indices = n.createVariable("lat_indices", "i4", ("tp_lat",))
    lat_indices[...] = [0, 8, 9, 17]

    lon_indices = n.createVariable("lon_indices", "i4", ("tp_lon",))
    lon_indices[...] = [0, 4, 7, 8, 11]

    # Dimension coordinates
    time = n.createVariable("time", "f4", ("time",))
    time.standard_name = "time"
    time.units = "days since 2000-01-01"
    time[...] = [0, 31]

    # Auxiliary coordinates
    reftime = n.createVariable("reftime", "f4", ("time",))
    reftime.standard_name = "forecast_reference_time"
    reftime.units = "days since 1900-01-01"
    reftime[...] = [31, 45]

    # Tie point coordinate variables
    lon = n.createVariable("lon", "f4", ("tp_lon",))
    lon.standard_name = "longitude"
    lon.units = "degrees_east"
    lon.bounds_tie_points = "lon_bounds"
    lon[...] = [15, 135, 225, 255, 345]

    lat = n.createVariable("lat", "f4", ("tp_lat",))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat.bounds_tie_points = "lat_bounds"
    lat[...] = [-85, -5, 5, 85]

    c = np.array(
        [
            [0, 4, 7, 8, 11],
            [96, 100, 103, 104, 107],
            [108, 112, 115, 116, 119],
            [204, 208, 211, 212, 215],
        ],
        dtype="float32",
    )

    a_2d = n.createVariable("a_2d", "f4", ("tp_lat", "tp_lon"))
    a_2d.units = "m"
    a_2d.bounds_tie_points = "a_2d_bounds"
    a_2d[...] = c

    b_2d = n.createVariable("b_2d", "f4", ("tp_lat", "tp_lon"))
    b_2d.units = "m"
    b_2d.bounds_tie_points = "b_2d_bounds"
    b_2d[...] = -c

    # Tie point bounds variables
    lat_bounds = n.createVariable("lat_bounds", "f4", ("tp_lat",))
    lat_bounds[...] = [-90, 0, 0, 90]

    lon_bounds = n.createVariable("lon_bounds", "f4", ("tp_lon",))
    lon_bounds[...] = [0, 150, 240, 240, 360]

    bounds_2d = np.array(
        [
            [0, 5, 8, 8, 12],
            [117, 122, 125, 125, 129],
            [117, 122, 125, 125, 129],
            [234, 239, 242, 242, 246],
        ],
        dtype="float32",
    )

    a_2d_bounds = n.createVariable("a_2d_bounds", "f4", ("tp_lat", "tp_lon"))
    a_2d_bounds[...] = bounds_2d

    b_2d_bounds = n.createVariable("b_2d_bounds", "f4", ("tp_lat", "tp_lon"))
    b_2d_bounds[...] = -bounds_2d

    # Interpolation variables
    linear_lat = n.createVariable("linear_lat", "i4", ())
    linear_lat.interpolation_name = "linear"
    linear_lat.computational_precision = "64"
    linear_lat.foo = "bar"
    linear_lat.tie_point_mapping = "lat: lat_indices tp_lat"

    linear_lon = n.createVariable("linear_lon", "i4", ())
    linear_lon.interpolation_name = "linear"
    linear_lon.computational_precision = "64"
    linear_lon.foo = "bar"
    linear_lon.tie_point_mapping = "lon: lon_indices tp_lon"

    bilinear = n.createVariable("bilinear", "i4", ())
    bilinear.interpolation_name = "bi_linear"
    bilinear.computational_precision = "64"
    bilinear.tie_point_mapping = (
        "lat: lat_indices tp_lat lon: lon_indices tp_lon"
    )

    quadratic_lat = n.createVariable("quadratic_lat", "i4", ())
    quadratic_lat.interpolation_name = "quadratic"
    quadratic_lat.computational_precision = "64"
    quadratic_lat.tie_point_mapping = "lat: lat_indices tp_lat subarea_lat"
    quadratic_lat.interpolation_parameters = "w: w_lat"

    quadratic_lon = n.createVariable("quadratic_lon", "i4", ())
    quadratic_lon.interpolation_name = "quadratic"
    quadratic_lon.computational_precision = "64"
    quadratic_lon.tie_point_mapping = "lon: lon_indices tp_lon subarea_lon"
    quadratic_lon.interpolation_parameters = "w: w_lon"

    general = n.createVariable("general", "i4", ())
    general.interpolation_description = "A new method"
    general.computational_precision = "64"
    general.tie_point_mapping = (
        "lat: lat_indices tp_lat lon: lon_indices tp_lon subarea_lon"
    )
    general.interpolation_parameters = "c: cp"

    # Interpolation parameters
    w_lat = n.createVariable("w_lat", "f8", ("subarea_lat",))
    w_lat.long_name = "quadratic interpolation coefficient (lat)"
    w_lat[...] = [1, 2]

    w_lon = n.createVariable("w_lon", "f8", ("subarea_lon",))
    w_lon.long_name = "quadratic interpolation coefficient (lon)"
    w_lon[...] = [10, 5, 15]

    cp = n.createVariable("cp", "f8", ("subarea_lon", "tp_lat"))
    cp.long_name = "interpolation coefficient (lon & lat)"
    cp[...] = np.arange(3 * 4).reshape(3, 4)

    # Data variables
    q = n.createVariable("q", "f4", ("lat", "lon"))
    q.standard_name = "specific_humidity"
    q.units = "1"
    q.coordinate_interpolation = (
        "lat: linear_lat " "lon: linear_lon " "a_2d: b_2d: bilinear"
    )
    q[...] = (np.arange(18 * 12).reshape(18, 12) / (18 * 12 + 1)).round(2)

    t = n.createVariable("t", "f4", ("time", "lat", "lon"))
    t.standard_name = "air_temperature"
    t.units = "K"
    t.coordinates = "reftime"
    t.coordinate_interpolation = (
        "lat: linear_lat " "lon: linear_lon " "a_2d: b_2d: bilinear"
    )
    t[...] = np.arange(2 * 18 * 12).reshape(2, 18, 12).round(0)

    t2 = n.createVariable("t2", "f4", ("time", "lat", "lon"))
    t2.standard_name = "air_temperature"
    t2.units = "K"
    t2.coordinates = "reftime"
    t2.coordinate_interpolation = (
        "lat: quadratic_lat " "lon: quadratic_lon " "a_2d: b_2d: bilinear"
    )
    t2[...] = np.arange(2 * 18 * 12).reshape(2, 18, 12).round(0)

    t3 = n.createVariable("t3", "f4", ("time", "lat", "lon"))
    t3.standard_name = "air_temperature"
    t3.units = "K"
    t3.coordinates = "reftime"
    t3.coordinate_interpolation = "a_2d: b_2d: general"
    t3[...] = np.arange(2 * 18 * 12).reshape(2, 18, 12).round(0)

    # Original coordinates
    rlon = n.createVariable("rlon", "f4", ("lon",))
    rlon.units = "degrees_east"
    rlon.bounds_tie_points = "rlon_bounds"
    rlon[...] = np.linspace(15, 345, 12)

    rlat = n.createVariable("rlat", "f4", ("lat",))
    rlat.units = "degrees_north"
    rlat.bounds_tie_points = "rlat_bounds"
    rlat[...] = np.linspace(-85, 85, 18)

    x = np.linspace(-90, 90, 19)

    rlat_bounds = n.createVariable("rlat_bounds", "f4", ("lat", "bounds2"))
    rlat_bounds.units = "degrees_north"
    rlat_bounds[...] = np.column_stack((x[:-1], x[1:]))

    x = np.linspace(0, 360, 13)

    rlon_bounds = n.createVariable("rlon_bounds", "f4", ("lon", "bounds2"))
    rlon_bounds.units = "degrees_east"
    rlon_bounds[...] = np.column_stack((x[:-1], x[1:]))

    ra_2d = n.createVariable("ra_2d", "f4", ("lat", "lon"))
    ra_2d.units = "m"
    ra_2d.bounds_tie_points = "ra_2d_bounds"
    ra_2d[...] = np.arange(18 * 12).reshape(18, 12)

    rb_2d = n.createVariable("rb_2d", "f4", ("lat", "lon"))
    rb_2d.units = "m"
    rb_2d.bounds_tie_points = "rb_2d_bounds"
    rb_2d[...] = -np.arange(18 * 12).reshape(18, 12)

    x = np.arange(19 * 13).reshape(19, 13)
    x = np.stack([x[:-1, :-1], x[:-1, 1:], x[1:, 1:], x[1:, :-1]], axis=2)

    ra_2d_bounds = n.createVariable(
        "ra_2d_bounds", "f4", ("lat", "lon", "bounds4")
    )
    ra_2d_bounds.units = "m"
    ra_2d_bounds[...] = x

    rb_2d_bounds = n.createVariable(
        "rb_2d_bounds", "f4", ("lat", "lon", "bounds4")
    )
    rb_2d_bounds.units = "m"
    rb_2d_bounds[...] = -x

    rlon_quadratic = n.createVariable("rlon_quadratic", "f4", ("lon",))
    rlon_quadratic.units = "degrees_east"
    rlon_quadratic.bounds_tie_points = "rlon_quadratic_bounds"
    rlon_quadratic[...] = np.array(
        [
            15.0,
            52.5,
            85.0,
            112.5,
            135.0,
            169.44444444,
            199.44444444,
            225.0,
            255.0,
            298.33333333,
            328.33333333,
            345.0,
        ]
    )

    rlat_quadratic = n.createVariable("rlat_quadratic", "f4", ("lat",))
    rlat_quadratic.units = "degrees_north"
    rlat_quadratic.bounds_tie_points = "rlat_quadratic_bounds"
    rlat_quadratic[...] = np.array(
        [
            -85.0,
            -74.5625,
            -64.25,
            -54.0625,
            -44.0,
            -34.0625,
            -24.25,
            -14.5625,
            -5.0,
            5.0,
            15.875,
            26.5,
            36.875,
            47.0,
            56.875,
            66.5,
            75.875,
            85.0,
        ]
    )

    x = np.array(
        [
            -90.0,
            -79.60493827,
            -69.30864198,
            -59.11111111,
            -49.01234568,
            -39.01234568,
            -29.11111111,
            -19.30864198,
            -9.60493827,
            0.0,
            10.79012346,
            21.38271605,
            31.77777778,
            41.97530864,
            51.97530864,
            61.77777778,
            71.38271605,
            80.79012346,
            90.0,
        ]
    )

    rlat_quadratic_bounds = n.createVariable(
        "rlat_quadratic_bounds", "f4", ("lat", "bounds2")
    )
    rlat_quadratic_bounds.units = "degrees_north"
    rlat_quadratic_bounds[...] = np.column_stack((x[:-1], x[1:]))

    x = np.array(
        [
            0.0,
            36.4,
            69.6,
            99.6,
            126.4,
            150.0,
            184.44444444,
            214.44444444,
            240.0,
            281.25,
            315.0,
            341.25,
            360.0,
        ]
    )

    rlon_quadratic_bounds = n.createVariable(
        "rlon_quadratic_bounds", "f4", ("lon", "bounds2")
    )
    rlon_quadratic_bounds.units = "degrees_east"
    rlon_quadratic_bounds[...] = np.column_stack((x[:-1], x[1:]))

    n.close()

    return filename


def _make_subsampled_2(filename):
    """Lossy compression by coordinate subsampling (2).

    Make a netCDF file with lossy compression by coordinate subsampling
    and reconstitution by bi_quadratic_latitude_longitude.

    """
    n = netCDF4.Dataset(filename, "w", format="NETCDF4")

    n.Conventions = f"CF-{VN}"
    n.comment = (
        "A netCDF file with lossy compression by coordinate subsampling "
        "and reconstitution by bi_quadratic_latitude_longitude."
    )

    # Dimensions
    n.createDimension("track", 48)
    n.createDimension("scan", 32)
    n.createDimension("tie_point_track", 6)
    n.createDimension("tie_point_scan", 3)
    n.createDimension("subarea_track", 4)
    n.createDimension("subarea_scan", 2)

    # Tie point index variables
    track_indices = n.createVariable(
        "track_indices", "i4", ("tie_point_track",)
    )
    track_indices[...] = [0, 15, 16, 31, 32, 47]

    scan_indices = n.createVariable("scan_indices", "i4", ("tie_point_scan",))
    scan_indices[...] = [0, 15, 31]

    # Tie point coordinate variables
    lon = n.createVariable("lon", "f4", ("tie_point_track", "tie_point_scan"))
    lon.standard_name = "longitude"
    lon.units = "degrees_east"
    lon[...] = [
        [-63.87722, -64.134476, -64.39908],
        [-63.883564, -64.14137, -64.40653],
        [-63.88726, -64.14484, -64.40984],
        [-63.893456, -64.15159, -64.41716],
        [-63.898563, -64.15655, -64.42192],
        [-63.90473, -64.163284, -64.42923],
    ]

    lat = n.createVariable("lat", "f4", ("tie_point_track", "tie_point_scan"))
    lat.standard_name = "latitude"
    lat.units = "degrees_north"
    lat[...] = [
        [31.443592, 31.437656, 31.431015],
        [31.664017, 31.655293, 31.645786],
        [31.546421, 31.540571, 31.534006],
        [31.766857, 31.758223, 31.748795],
        [31.648563, 31.642809, 31.636333],
        [31.868998, 31.86045, 31.851114],
    ]

    # Tie point bounds variables

    # Reconstituded coordinates
    rec_lon = n.createVariable("rec_lon", "f4", ("track", "scan"))
    rec_lon.standard_name = "longitude"
    rec_lon.units = "degrees_east"
    rec_lon[...] = np.array(
        [
            [
                -63.87722,
                -63.894657,
                -63.912052,
                -63.92941,
                -63.946724,
                -63.963997,
                -63.981228,
                -63.99842,
                -64.01557,
                -64.03268,
                -64.04975,
                -64.06677,
                -64.08376,
                -64.10071,
                -64.117615,
                -64.134476,
                -64.1513,
                -64.16808,
                -64.18483,
                -64.20154,
                -64.21821,
                -64.23484,
                -64.251434,
                -64.26799,
                -64.284515,
                -64.300995,
                -64.31744,
                -64.33384,
                -64.350204,
                -64.36653,
                -64.38283,
                -64.39908,
            ],
            [
                -63.877724,
                -63.895164,
                -63.912563,
                -63.92992,
                -63.947235,
                -63.96451,
                -63.981747,
                -63.998943,
                -64.0161,
                -64.0332,
                -64.05028,
                -64.06731,
                -64.0843,
                -64.10124,
                -64.11815,
                -64.13502,
                -64.15183,
                -64.168625,
                -64.18537,
                -64.20208,
                -64.21876,
                -64.23539,
                -64.25199,
                -64.26855,
                -64.28507,
                -64.30155,
                -64.318,
                -64.334404,
                -64.35078,
                -64.3671,
                -64.3834,
                -64.39965,
            ],
            [
                -63.878216,
                -63.895657,
                -63.91306,
                -63.93042,
                -63.94774,
                -63.96502,
                -63.982254,
                -63.99945,
                -64.01661,
                -64.03372,
                -64.0508,
                -64.067825,
                -64.084816,
                -64.10177,
                -64.118675,
                -64.135544,
                -64.15236,
                -64.16915,
                -64.185905,
                -64.20262,
                -64.21929,
                -64.23593,
                -64.25253,
                -64.26909,
                -64.285614,
                -64.3021,
                -64.31855,
                -64.33496,
                -64.351326,
                -64.36766,
                -64.38396,
                -64.400215,
            ],
            [
                -63.878696,
                -63.89614,
                -63.913544,
                -63.93091,
                -63.94823,
                -63.96551,
                -63.98275,
                -63.99995,
                -64.017105,
                -64.034225,
                -64.0513,
                -64.06834,
                -64.08533,
                -64.10228,
                -64.119194,
                -64.13606,
                -64.15288,
                -64.16967,
                -64.186424,
                -64.20314,
                -64.21982,
                -64.23646,
                -64.25306,
                -64.26962,
                -64.28615,
                -64.30264,
                -64.31909,
                -64.3355,
                -64.351875,
                -64.36821,
                -64.38451,
                -64.400764,
            ],
            [
                -63.879166,
                -63.896614,
                -63.91402,
                -63.931385,
                -63.948708,
                -63.965992,
                -63.983234,
                -64.000435,
                -64.01759,
                -64.03471,
                -64.051796,
                -64.06883,
                -64.08583,
                -64.10278,
                -64.1197,
                -64.136566,
                -64.15339,
                -64.17018,
                -64.186935,
                -64.20365,
                -64.22034,
                -64.23698,
                -64.253586,
                -64.27015,
                -64.286674,
                -64.30317,
                -64.31962,
                -64.33603,
                -64.35241,
                -64.368744,
                -64.38505,
                -64.401306,
            ],
            [
                -63.879623,
                -63.89707,
                -63.914482,
                -63.93185,
                -63.949177,
                -63.96646,
                -63.983707,
                -64.00091,
                -64.018074,
                -64.035194,
                -64.05228,
                -64.06931,
                -64.08631,
                -64.10327,
                -64.120186,
                -64.13706,
                -64.15388,
                -64.17068,
                -64.18744,
                -64.204155,
                -64.22084,
                -64.23749,
                -64.25409,
                -64.27066,
                -64.28719,
                -64.30368,
                -64.32014,
                -64.336555,
                -64.35293,
                -64.36927,
                -64.385574,
                -64.40184,
            ],
            [
                -63.88007,
                -63.897522,
                -63.914932,
                -63.932304,
                -63.949635,
                -63.966923,
                -63.98417,
                -64.00137,
                -64.01854,
                -64.03567,
                -64.05275,
                -64.069786,
                -64.08679,
                -64.10375,
                -64.12067,
                -64.13754,
                -64.154366,
                -64.171165,
                -64.18793,
                -64.20465,
                -64.22134,
                -64.23798,
                -64.25459,
                -64.271164,
                -64.2877,
                -64.30419,
                -64.32065,
                -64.33707,
                -64.35345,
                -64.36979,
                -64.38609,
                -64.40236,
            ],
            [
                -63.880505,
                -63.897957,
                -63.91537,
                -63.932747,
                -63.950077,
                -63.96737,
                -63.98462,
                -64.00183,
                -64.019,
                -64.036125,
                -64.05321,
                -64.07025,
                -64.08726,
                -64.10422,
                -64.12114,
                -64.138016,
                -64.15484,
                -64.17164,
                -64.1884,
                -64.20513,
                -64.22182,
                -64.238464,
                -64.25508,
                -64.27165,
                -64.288185,
                -64.30468,
                -64.321144,
                -64.33756,
                -64.35394,
                -64.37029,
                -64.3866,
                -64.40286,
            ],
            [
                -63.880928,
                -63.898384,
                -63.915802,
                -63.933178,
                -63.950512,
                -63.967804,
                -63.985058,
                -64.002266,
                -64.01944,
                -64.03657,
                -64.05366,
                -64.0707,
                -64.08771,
                -64.104675,
                -64.1216,
                -64.13848,
                -64.155304,
                -64.172104,
                -64.18887,
                -64.2056,
                -64.22229,
                -64.23894,
                -64.255554,
                -64.27213,
                -64.288666,
                -64.30517,
                -64.321625,
                -64.33805,
                -64.35444,
                -64.37078,
                -64.38709,
                -64.40336,
            ],
            [
                -63.881336,
                -63.8988,
                -63.916218,
                -63.933598,
                -63.95093,
                -63.968227,
                -63.985485,
                -64.0027,
                -64.01987,
                -64.037,
                -64.05409,
                -64.071144,
                -64.08815,
                -64.10512,
                -64.12204,
                -64.13892,
                -64.155754,
                -64.17256,
                -64.18932,
                -64.206055,
                -64.22275,
                -64.2394,
                -64.25602,
                -64.2726,
                -64.28914,
                -64.30564,
                -64.322105,
                -64.338524,
                -64.35491,
                -64.37126,
                -64.38757,
                -64.40385,
            ],
            [
                -63.881737,
                -63.8992,
                -63.916622,
                -63.934002,
                -63.951344,
                -63.96864,
                -63.985897,
                -64.00311,
                -64.02029,
                -64.03742,
                -64.05452,
                -64.07157,
                -64.08858,
                -64.105545,
                -64.122475,
                -64.139366,
                -64.15619,
                -64.173004,
                -64.18977,
                -64.206505,
                -64.2232,
                -64.23985,
                -64.25648,
                -64.273056,
                -64.2896,
                -64.3061,
                -64.32256,
                -64.339,
                -64.355385,
                -64.371735,
                -64.38805,
                -64.40432,
            ],
            [
                -63.882126,
                -63.899593,
                -63.917015,
                -63.9344,
                -63.95174,
                -63.969044,
                -63.9863,
                -64.00352,
                -64.0207,
                -64.037834,
                -64.05493,
                -64.07198,
                -64.089,
                -64.105965,
                -64.1229,
                -64.139786,
                -64.156624,
                -64.17343,
                -64.19021,
                -64.20694,
                -64.22364,
                -64.240295,
                -64.25692,
                -64.2735,
                -64.29005,
                -64.30655,
                -64.32302,
                -64.33945,
                -64.35584,
                -64.37219,
                -64.38851,
                -64.404785,
            ],
            [
                -63.882504,
                -63.89997,
                -63.917397,
                -63.934784,
                -63.95213,
                -63.969433,
                -63.986694,
                -64.003914,
                -64.021095,
                -64.03823,
                -64.05533,
                -64.07239,
                -64.0894,
                -64.10638,
                -64.123314,
                -64.140205,
                -64.15704,
                -64.17385,
                -64.19063,
                -64.20737,
                -64.22407,
                -64.24073,
                -64.25735,
                -64.27393,
                -64.29048,
                -64.30699,
                -64.32346,
                -64.3399,
                -64.356285,
                -64.37264,
                -64.38896,
                -64.40524,
            ],
            [
                -63.88287,
                -63.900337,
                -63.917767,
                -63.935158,
                -63.952503,
                -63.96981,
                -63.987076,
                -64.004295,
                -64.02148,
                -64.03862,
                -64.055725,
                -64.07278,
                -64.0898,
                -64.10677,
                -64.12371,
                -64.1406,
                -64.15745,
                -64.17426,
                -64.19104,
                -64.20778,
                -64.22449,
                -64.24115,
                -64.257774,
                -64.27436,
                -64.29091,
                -64.30742,
                -64.32389,
                -64.340324,
                -64.35672,
                -64.373085,
                -64.389404,
                -64.405685,
            ],
            [
                -63.88322,
                -63.900696,
                -63.91813,
                -63.935516,
                -63.952866,
                -63.970177,
                -63.987442,
                -64.00467,
                -64.02185,
                -64.038994,
                -64.0561,
                -64.07316,
                -64.09018,
                -64.10716,
                -64.1241,
                -64.14099,
                -64.157845,
                -64.17466,
                -64.191444,
                -64.20818,
                -64.22489,
                -64.241554,
                -64.25819,
                -64.27477,
                -64.29132,
                -64.30784,
                -64.32431,
                -64.34075,
                -64.35715,
                -64.37351,
                -64.38983,
                -64.40611,
            ],
            [
                -63.883564,
                -63.90104,
                -63.918476,
                -63.935867,
                -63.95322,
                -63.97053,
                -63.9878,
                -64.00503,
                -64.02222,
                -64.03936,
                -64.056465,
                -64.07353,
                -64.09055,
                -64.10754,
                -64.12447,
                -64.14137,
                -64.15823,
                -64.17505,
                -64.19183,
                -64.20858,
                -64.22529,
                -64.24195,
                -64.25858,
                -64.27518,
                -64.29173,
                -64.30824,
                -64.32472,
                -64.34116,
                -64.35756,
                -64.373924,
                -64.39025,
                -64.40653,
            ],
            [
                -63.88726,
                -63.90472,
                -63.92214,
                -63.939514,
                -63.956852,
                -63.974148,
                -63.9914,
                -64.00861,
                -64.02579,
                -64.042915,
                -64.060005,
                -64.07706,
                -64.09406,
                -64.11103,
                -64.12795,
                -64.14484,
                -64.16168,
                -64.17849,
                -64.19527,
                -64.212,
                -64.2287,
                -64.24535,
                -64.26197,
                -64.27856,
                -64.2951,
                -64.31161,
                -64.32807,
                -64.344505,
                -64.36089,
                -64.37725,
                -64.39356,
                -64.40984,
            ],
            [
                -63.887756,
                -63.905216,
                -63.922638,
                -63.940018,
                -63.957355,
                -63.97465,
                -63.99191,
                -64.009125,
                -64.0263,
                -64.043434,
                -64.060524,
                -64.077576,
                -64.09458,
                -64.11156,
                -64.12848,
                -64.14537,
                -64.16221,
                -64.17902,
                -64.1958,
                -64.21253,
                -64.22923,
                -64.245895,
                -64.26252,
                -64.2791,
                -64.29565,
                -64.31216,
                -64.32863,
                -64.34506,
                -64.36145,
                -64.37781,
                -64.39413,
                -64.41041,
            ],
            [
                -63.888237,
                -63.9057,
                -63.923126,
                -63.940506,
                -63.957848,
                -63.975147,
                -63.99241,
                -64.00963,
                -64.0268,
                -64.04394,
                -64.06103,
                -64.07809,
                -64.0951,
                -64.11207,
                -64.129,
                -64.14589,
                -64.16273,
                -64.17954,
                -64.19632,
                -64.21306,
                -64.22976,
                -64.24642,
                -64.263054,
                -64.27964,
                -64.29619,
                -64.3127,
                -64.32917,
                -64.345604,
                -64.362,
                -64.37836,
                -64.39468,
                -64.41096,
            ],
            [
                -63.88871,
                -63.906174,
                -63.9236,
                -63.940987,
                -63.95833,
                -63.97563,
                -63.992893,
                -64.01012,
                -64.02729,
                -64.04443,
                -64.06153,
                -64.07858,
                -64.0956,
                -64.11257,
                -64.1295,
                -64.14639,
                -64.16324,
                -64.18005,
                -64.19683,
                -64.21358,
                -64.23028,
                -64.24695,
                -64.26357,
                -64.28016,
                -64.296715,
                -64.313225,
                -64.329704,
                -64.34614,
                -64.36253,
                -64.3789,
                -64.39522,
                -64.4115,
            ],
            [
                -63.889168,
                -63.906635,
                -63.924065,
                -63.941452,
                -63.958797,
                -63.976105,
                -63.993366,
                -64.01059,
                -64.02777,
                -64.044914,
                -64.06201,
                -64.07907,
                -64.096085,
                -64.11306,
                -64.13,
                -64.14689,
                -64.163734,
                -64.18055,
                -64.197334,
                -64.21408,
                -64.23078,
                -64.24745,
                -64.264084,
                -64.28068,
                -64.297226,
                -64.31374,
                -64.33022,
                -64.34666,
                -64.36306,
                -64.379425,
                -64.395744,
                -64.41203,
            ],
            [
                -63.889614,
                -63.90709,
                -63.92452,
                -63.94191,
                -63.959255,
                -63.976562,
                -63.99383,
                -64.011055,
                -64.02824,
                -64.04538,
                -64.062485,
                -64.079544,
                -64.096565,
                -64.11354,
                -64.13048,
                -64.14738,
                -64.164215,
                -64.18104,
                -64.19782,
                -64.21457,
                -64.23128,
                -64.24795,
                -64.26458,
                -64.28117,
                -64.29773,
                -64.31425,
                -64.33073,
                -64.34717,
                -64.36357,
                -64.37994,
                -64.39626,
                -64.41255,
            ],
            [
                -63.890053,
                -63.907528,
                -63.92496,
                -63.942352,
                -63.9597,
                -63.977013,
                -63.99428,
                -64.01151,
                -64.028694,
                -64.045845,
                -64.06294,
                -64.08001,
                -64.09703,
                -64.11401,
                -64.13095,
                -64.14785,
                -64.164696,
                -64.18152,
                -64.1983,
                -64.21506,
                -64.231766,
                -64.248436,
                -64.265076,
                -64.28167,
                -64.298225,
                -64.31474,
                -64.33123,
                -64.34767,
                -64.364075,
                -64.38045,
                -64.396774,
                -64.41306,
            ],
            [
                -63.890476,
                -63.907955,
                -63.92539,
                -63.942783,
                -63.960136,
                -63.97745,
                -63.99472,
                -64.011955,
                -64.029144,
                -64.04629,
                -64.06339,
                -64.08046,
                -64.09749,
                -64.11447,
                -64.13141,
                -64.148315,
                -64.16515,
                -64.181984,
                -64.19878,
                -64.21552,
                -64.23224,
                -64.24892,
                -64.26555,
                -64.28215,
                -64.298706,
                -64.31523,
                -64.33172,
                -64.34816,
                -64.36457,
                -64.38094,
                -64.39727,
                -64.41357,
            ],
            [
                -63.89089,
                -63.908367,
                -63.925808,
                -63.943203,
                -63.96056,
                -63.977875,
                -63.99515,
                -64.01238,
                -64.02957,
                -64.04672,
                -64.063835,
                -64.0809,
                -64.09793,
                -64.114914,
                -64.13186,
                -64.148766,
                -64.16561,
                -64.18244,
                -64.199234,
                -64.21599,
                -64.232704,
                -64.24938,
                -64.266014,
                -64.282616,
                -64.29918,
                -64.315704,
                -64.33219,
                -64.34864,
                -64.36505,
                -64.381424,
                -64.39776,
                -64.414055,
            ],
            [
                -63.891293,
                -63.90877,
                -63.926212,
                -63.943615,
                -63.96097,
                -63.97829,
                -63.995567,
                -64.0128,
                -64.03,
                -64.04715,
                -64.06426,
                -64.08133,
                -64.09836,
                -64.11535,
                -64.13229,
                -64.1492,
                -64.16605,
                -64.182884,
                -64.19968,
                -64.21643,
                -64.233154,
                -64.24983,
                -64.26647,
                -64.28308,
                -64.299644,
                -64.31617,
                -64.33266,
                -64.349106,
                -64.365524,
                -64.3819,
                -64.39823,
                -64.41453,
            ],
            [
                -63.89168,
                -63.909164,
                -63.92661,
                -63.94401,
                -63.961372,
                -63.978695,
                -63.99597,
                -64.01321,
                -64.0304,
                -64.04756,
                -64.064674,
                -64.08175,
                -64.09878,
                -64.11577,
                -64.13272,
                -64.14963,
                -64.16648,
                -64.18332,
                -64.20011,
                -64.21687,
                -64.23359,
                -64.250275,
                -64.26692,
                -64.28352,
                -64.300095,
                -64.31662,
                -64.333115,
                -64.34956,
                -64.36598,
                -64.382355,
                -64.3987,
                -64.41499,
            ],
            [
                -63.89206,
                -63.909546,
                -63.926994,
                -63.944397,
                -63.96176,
                -63.979084,
                -63.996365,
                -64.0136,
                -64.03081,
                -64.047966,
                -64.06508,
                -64.08215,
                -64.09919,
                -64.11618,
                -64.13313,
                -64.15005,
                -64.1669,
                -64.18374,
                -64.20054,
                -64.2173,
                -64.234024,
                -64.25071,
                -64.26736,
                -64.28396,
                -64.30053,
                -64.31706,
                -64.33356,
                -64.35001,
                -64.36643,
                -64.382805,
                -64.39915,
                -64.41545,
            ],
            [
                -63.892426,
                -63.909916,
                -63.927364,
                -63.94477,
                -63.96214,
                -63.979465,
                -63.996746,
                -64.01399,
                -64.03119,
                -64.048355,
                -64.06547,
                -64.08255,
                -64.09959,
                -64.116585,
                -64.13354,
                -64.15045,
                -64.16731,
                -64.18415,
                -64.20095,
                -64.21771,
                -64.23444,
                -64.25113,
                -64.26778,
                -64.284386,
                -64.30096,
                -64.3175,
                -64.33399,
                -64.35045,
                -64.36687,
                -64.38325,
                -64.39959,
                -64.41589,
            ],
            [
                -63.89278,
                -63.910275,
                -63.927727,
                -63.945133,
                -63.962505,
                -63.97983,
                -63.997116,
                -64.014366,
                -64.03157,
                -64.04873,
                -64.06585,
                -64.08293,
                -64.09997,
                -64.11697,
                -64.13393,
                -64.15084,
                -64.16771,
                -64.18455,
                -64.201355,
                -64.218124,
                -64.23485,
                -64.25154,
                -64.26819,
                -64.284805,
                -64.30138,
                -64.31792,
                -64.33441,
                -64.35087,
                -64.367294,
                -64.383675,
                -64.400024,
                -64.41633,
            ],
            [
                -63.893124,
                -63.910618,
                -63.928074,
                -63.945488,
                -63.962856,
                -63.980186,
                -63.99748,
                -64.014725,
                -64.03193,
                -64.049095,
                -64.06622,
                -64.083305,
                -64.10034,
                -64.11735,
                -64.13431,
                -64.15122,
                -64.1681,
                -64.184944,
                -64.201744,
                -64.21851,
                -64.235245,
                -64.25194,
                -64.26859,
                -64.28521,
                -64.30178,
                -64.31832,
                -64.33482,
                -64.35129,
                -64.36771,
                -64.384094,
                -64.400444,
                -64.41675,
            ],
            [
                -63.893456,
                -63.910954,
                -63.92841,
                -63.945827,
                -63.9632,
                -63.980534,
                -63.997826,
                -64.015076,
                -64.03228,
                -64.04945,
                -64.066574,
                -64.083664,
                -64.10071,
                -64.11771,
                -64.134674,
                -64.15159,
                -64.16847,
                -64.18532,
                -64.202126,
                -64.2189,
                -64.235634,
                -64.25233,
                -64.26898,
                -64.2856,
                -64.30218,
                -64.31872,
                -64.33522,
                -64.351685,
                -64.36811,
                -64.3845,
                -64.40085,
                -64.41716,
            ],
            [
                -63.898563,
                -63.91605,
                -63.933495,
                -63.9509,
                -63.968266,
                -63.985588,
                -64.00287,
                -64.02011,
                -64.03731,
                -64.05447,
                -64.07158,
                -64.08866,
                -64.10569,
                -64.12269,
                -64.13964,
                -64.15655,
                -64.173416,
                -64.190254,
                -64.20705,
                -64.22381,
                -64.240524,
                -64.25721,
                -64.27385,
                -64.29046,
                -64.30702,
                -64.323555,
                -64.34004,
                -64.35649,
                -64.37291,
                -64.38928,
                -64.405624,
                -64.42192,
            ],
            [
                -63.899055,
                -63.916546,
                -63.933994,
                -63.9514,
                -63.968765,
                -63.98609,
                -64.00337,
                -64.020615,
                -64.03782,
                -64.05498,
                -64.0721,
                -64.08918,
                -64.10622,
                -64.12321,
                -64.14017,
                -64.15708,
                -64.17394,
                -64.19078,
                -64.20758,
                -64.22434,
                -64.241066,
                -64.257744,
                -64.27439,
                -64.291,
                -64.30757,
                -64.324104,
                -64.34059,
                -64.35705,
                -64.37347,
                -64.38985,
                -64.40618,
                -64.422485,
            ],
            [
                -63.899536,
                -63.917027,
                -63.93448,
                -63.95189,
                -63.969257,
                -63.986584,
                -64.00387,
                -64.02112,
                -64.03832,
                -64.05548,
                -64.07261,
                -64.08968,
                -64.10673,
                -64.123726,
                -64.14068,
                -64.15759,
                -64.17446,
                -64.1913,
                -64.2081,
                -64.22487,
                -64.24159,
                -64.25828,
                -64.274925,
                -64.291534,
                -64.308105,
                -64.32464,
                -64.34113,
                -64.35759,
                -64.37401,
                -64.39039,
                -64.40674,
                -64.42304,
            ],
            [
                -63.900005,
                -63.9175,
                -63.93495,
                -63.952366,
                -63.969738,
                -63.98707,
                -64.00436,
                -64.02161,
                -64.03881,
                -64.05598,
                -64.0731,
                -64.09018,
                -64.10722,
                -64.12423,
                -64.14118,
                -64.158104,
                -64.174965,
                -64.19181,
                -64.20861,
                -64.22538,
                -64.2421,
                -64.2588,
                -64.275444,
                -64.29206,
                -64.30863,
                -64.325165,
                -64.34167,
                -64.35812,
                -64.37455,
                -64.39093,
                -64.40727,
                -64.423584,
            ],
            [
                -63.900463,
                -63.91796,
                -63.935417,
                -63.95283,
                -63.970203,
                -63.987537,
                -64.00483,
                -64.02208,
                -64.03929,
                -64.05646,
                -64.073586,
                -64.09067,
                -64.10771,
                -64.12472,
                -64.14168,
                -64.1586,
                -64.17546,
                -64.19231,
                -64.209114,
                -64.22588,
                -64.24261,
                -64.2593,
                -64.275955,
                -64.29257,
                -64.30914,
                -64.32568,
                -64.342186,
                -64.35865,
                -64.37507,
                -64.39146,
                -64.40781,
                -64.42411,
            ],
            [
                -63.90091,
                -63.918407,
                -63.935867,
                -63.953285,
                -63.97066,
                -63.987995,
                -64.00529,
                -64.022545,
                -64.03976,
                -64.05692,
                -64.07405,
                -64.09114,
                -64.10819,
                -64.1252,
                -64.14216,
                -64.15908,
                -64.17595,
                -64.192795,
                -64.2096,
                -64.22637,
                -64.2431,
                -64.259796,
                -64.27645,
                -64.29307,
                -64.30965,
                -64.32619,
                -64.34269,
                -64.35915,
                -64.37558,
                -64.39197,
                -64.40832,
                -64.42463,
            ],
            [
                -63.901344,
                -63.918846,
                -63.936306,
                -63.953728,
                -63.971107,
                -63.988445,
                -64.00574,
                -64.022995,
                -64.04021,
                -64.05738,
                -64.07452,
                -64.091606,
                -64.10866,
                -64.12566,
                -64.14263,
                -64.15955,
                -64.17642,
                -64.19327,
                -64.21008,
                -64.22685,
                -64.24359,
                -64.260284,
                -64.27694,
                -64.293564,
                -64.31014,
                -64.32668,
                -64.34319,
                -64.35966,
                -64.37608,
                -64.39248,
                -64.40883,
                -64.42514,
            ],
            [
                -63.901768,
                -63.91927,
                -63.936733,
                -63.95416,
                -63.97154,
                -63.98888,
                -64.00618,
                -64.02344,
                -64.04066,
                -64.05783,
                -64.07497,
                -64.09206,
                -64.10911,
                -64.12612,
                -64.14309,
                -64.16001,
                -64.17689,
                -64.19373,
                -64.21055,
                -64.227325,
                -64.24406,
                -64.26076,
                -64.27742,
                -64.29404,
                -64.31062,
                -64.32717,
                -64.34367,
                -64.360146,
                -64.37658,
                -64.39297,
                -64.409325,
                -64.42564,
            ],
            [
                -63.902176,
                -63.919685,
                -63.937153,
                -63.95458,
                -63.971962,
                -63.989304,
                -64.00661,
                -64.023865,
                -64.041084,
                -64.058266,
                -64.0754,
                -64.09249,
                -64.10955,
                -64.126564,
                -64.14353,
                -64.16046,
                -64.17734,
                -64.19419,
                -64.211006,
                -64.22778,
                -64.24452,
                -64.26122,
                -64.277885,
                -64.29451,
                -64.3111,
                -64.327644,
                -64.344154,
                -64.36063,
                -64.37706,
                -64.39345,
                -64.409805,
                -64.426125,
            ],
            [
                -63.902576,
                -63.920086,
                -63.937557,
                -63.954983,
                -63.97237,
                -63.989716,
                -64.00702,
                -64.024284,
                -64.041504,
                -64.058685,
                -64.07582,
                -64.092926,
                -64.10998,
                -64.12699,
                -64.14397,
                -64.160904,
                -64.17777,
                -64.19463,
                -64.21145,
                -64.22823,
                -64.24497,
                -64.26167,
                -64.27834,
                -64.29497,
                -64.311554,
                -64.3281,
                -64.34462,
                -64.36109,
                -64.377525,
                -64.39392,
                -64.41028,
                -64.426605,
            ],
            [
                -63.902966,
                -63.92048,
                -63.93795,
                -63.95538,
                -63.97277,
                -63.990116,
                -64.00742,
                -64.02469,
                -64.041916,
                -64.0591,
                -64.07624,
                -64.09334,
                -64.1104,
                -64.12742,
                -64.144394,
                -64.16132,
                -64.17821,
                -64.19506,
                -64.21188,
                -64.22867,
                -64.24541,
                -64.262115,
                -64.278786,
                -64.29541,
                -64.312004,
                -64.32856,
                -64.34507,
                -64.36155,
                -64.37798,
                -64.39439,
                -64.41074,
                -64.42707,
            ],
            [
                -63.903343,
                -63.920856,
                -63.93833,
                -63.955765,
                -63.973156,
                -63.990505,
                -64.00781,
                -64.025085,
                -64.04231,
                -64.059494,
                -64.07664,
                -64.09374,
                -64.1108,
                -64.12782,
                -64.144806,
                -64.16174,
                -64.17863,
                -64.19549,
                -64.21231,
                -64.229095,
                -64.24584,
                -64.26255,
                -64.27921,
                -64.295845,
                -64.31244,
                -64.328995,
                -64.34551,
                -64.36199,
                -64.37843,
                -64.39484,
                -64.411194,
                -64.42752,
            ],
            [
                -63.903706,
                -63.921227,
                -63.9387,
                -63.95614,
                -63.973534,
                -63.990887,
                -64.008194,
                -64.02547,
                -64.042694,
                -64.05988,
                -64.077034,
                -64.09414,
                -64.1112,
                -64.12822,
                -64.1452,
                -64.16215,
                -64.17903,
                -64.19589,
                -64.21272,
                -64.22951,
                -64.246254,
                -64.26297,
                -64.27964,
                -64.29627,
                -64.31287,
                -64.32942,
                -64.34595,
                -64.36243,
                -64.37887,
                -64.39527,
                -64.41164,
                -64.42796,
            ],
            [
                -63.90406,
                -63.92158,
                -63.93906,
                -63.956497,
                -63.973896,
                -63.991253,
                -64.00857,
                -64.02584,
                -64.04307,
                -64.06026,
                -64.07741,
                -64.09452,
                -64.11158,
                -64.12861,
                -64.14559,
                -64.16254,
                -64.17943,
                -64.1963,
                -64.21312,
                -64.22991,
                -64.246666,
                -64.263374,
                -64.28005,
                -64.296684,
                -64.313286,
                -64.32984,
                -64.34637,
                -64.36285,
                -64.379295,
                -64.3957,
                -64.41207,
                -64.4284,
            ],
            [
                -63.904404,
                -63.921925,
                -63.939407,
                -63.95685,
                -63.974247,
                -63.991608,
                -64.00893,
                -64.0262,
                -64.043434,
                -64.06062,
                -64.077774,
                -64.09489,
                -64.11195,
                -64.12898,
                -64.14597,
                -64.16292,
                -64.17982,
                -64.196686,
                -64.21351,
                -64.2303,
                -64.247055,
                -64.26377,
                -64.28045,
                -64.29709,
                -64.31369,
                -64.33025,
                -64.34678,
                -64.36326,
                -64.37971,
                -64.39612,
                -64.41248,
                -64.42882,
            ],
            [
                -63.90473,
                -63.92226,
                -63.939743,
                -63.957188,
                -63.974586,
                -63.991947,
                -64.00927,
                -64.02654,
                -64.043785,
                -64.06098,
                -64.07813,
                -64.095245,
                -64.11232,
                -64.12935,
                -64.14633,
                -64.163284,
                -64.18019,
                -64.19706,
                -64.21389,
                -64.23069,
                -64.247444,
                -64.26416,
                -64.28084,
                -64.29748,
                -64.31409,
                -64.33065,
                -64.347176,
                -64.36366,
                -64.38011,
                -64.39652,
                -64.412895,
                -64.42923,
            ],
        ],
        dtype="float32",
    )

    rec_lat = n.createVariable("rec_lat", "f4", ("track", "scan"))
    rec_lat.standard_name = "latitude"
    rec_lat.units = "degrees_north"
    rec_lat[...] = np.array(
        [
            [
                31.443592,
                31.443207,
                31.44282,
                31.44243,
                31.44204,
                31.441648,
                31.441254,
                31.44086,
                31.440464,
                31.440067,
                31.439669,
                31.439268,
                31.438868,
                31.438465,
                31.43806,
                31.437656,
                31.43725,
                31.436842,
                31.436434,
                31.436024,
                31.435614,
                31.4352,
                31.434788,
                31.434372,
                31.433956,
                31.43354,
                31.43312,
                31.432703,
                31.432281,
                31.43186,
                31.431438,
                31.431015,
            ],
            [
                31.458286,
                31.457888,
                31.457487,
                31.457085,
                31.456682,
                31.456278,
                31.455872,
                31.455465,
                31.455057,
                31.454647,
                31.454237,
                31.453825,
                31.453411,
                31.452995,
                31.45258,
                31.452164,
                31.451744,
                31.451324,
                31.450905,
                31.450483,
                31.45006,
                31.449635,
                31.44921,
                31.448784,
                31.448355,
                31.447927,
                31.447496,
                31.447065,
                31.446632,
                31.4462,
                31.445766,
                31.44533,
            ],
            [
                31.472979,
                31.472569,
                31.472155,
                31.47174,
                31.471325,
                31.47091,
                31.47049,
                31.470072,
                31.46965,
                31.469229,
                31.468805,
                31.46838,
                31.467955,
                31.467527,
                31.4671,
                31.46667,
                31.46624,
                31.465809,
                31.465376,
                31.464941,
                31.464506,
                31.46407,
                31.463633,
                31.463194,
                31.462755,
                31.462313,
                31.461872,
                31.46143,
                31.460985,
                31.460539,
                31.460093,
                31.459646,
            ],
            [
                31.487673,
                31.48725,
                31.486824,
                31.486397,
                31.485968,
                31.485538,
                31.48511,
                31.484676,
                31.484243,
                31.483809,
                31.483374,
                31.482937,
                31.482498,
                31.48206,
                31.481619,
                31.481178,
                31.480736,
                31.480291,
                31.479847,
                31.4794,
                31.478954,
                31.478506,
                31.478056,
                31.477606,
                31.477154,
                31.476702,
                31.476248,
                31.475792,
                31.475336,
                31.47488,
                31.474422,
                31.473963,
            ],
            [
                31.502365,
                31.50193,
                31.501492,
                31.501053,
                31.500612,
                31.50017,
                31.499727,
                31.499283,
                31.498837,
                31.49839,
                31.497942,
                31.497494,
                31.497044,
                31.496592,
                31.49614,
                31.495686,
                31.495232,
                31.494776,
                31.494318,
                31.49386,
                31.4934,
                31.49294,
                31.49248,
                31.492016,
                31.491552,
                31.491089,
                31.490623,
                31.490156,
                31.489689,
                31.48922,
                31.48875,
                31.48828,
            ],
            [
                31.51706,
                31.516611,
                31.516161,
                31.515709,
                31.515255,
                31.514801,
                31.514345,
                31.51389,
                31.513432,
                31.512972,
                31.512512,
                31.51205,
                31.511587,
                31.511124,
                31.510658,
                31.510193,
                31.509727,
                31.509258,
                31.50879,
                31.50832,
                31.507849,
                31.507376,
                31.506903,
                31.506428,
                31.505953,
                31.505476,
                31.505,
                31.50452,
                31.50404,
                31.503561,
                31.503078,
                31.502596,
            ],
            [
                31.531754,
                31.531292,
                31.530828,
                31.530365,
                31.5299,
                31.529432,
                31.528965,
                31.528496,
                31.528025,
                31.527554,
                31.52708,
                31.526608,
                31.526133,
                31.525656,
                31.525179,
                31.524702,
                31.524223,
                31.523743,
                31.523262,
                31.52278,
                31.522297,
                31.521812,
                31.521326,
                31.52084,
                31.520353,
                31.519865,
                31.519375,
                31.518885,
                31.518393,
                31.5179,
                31.517408,
                31.516914,
            ],
            [
                31.546448,
                31.545975,
                31.545498,
                31.545021,
                31.544544,
                31.544064,
                31.543585,
                31.543102,
                31.54262,
                31.542135,
                31.54165,
                31.541164,
                31.540678,
                31.54019,
                31.5397,
                31.53921,
                31.53872,
                31.538227,
                31.537733,
                31.53724,
                31.536745,
                31.536247,
                31.535751,
                31.535252,
                31.534754,
                31.534252,
                31.53375,
                31.533249,
                31.532745,
                31.532242,
                31.531736,
                31.53123,
            ],
            [
                31.561144,
                31.560656,
                31.560167,
                31.559679,
                31.559189,
                31.558697,
                31.558203,
                31.557709,
                31.557215,
                31.556719,
                31.556221,
                31.555723,
                31.555223,
                31.554722,
                31.55422,
                31.553719,
                31.553215,
                31.552711,
                31.552206,
                31.5517,
                31.551193,
                31.550684,
                31.550175,
                31.549665,
                31.549154,
                31.548641,
                31.548128,
                31.547615,
                31.5471,
                31.546583,
                31.546066,
                31.54555,
            ],
            [
                31.575838,
                31.575338,
                31.574839,
                31.574335,
                31.573833,
                31.573328,
                31.572823,
                31.572317,
                31.57181,
                31.5713,
                31.570791,
                31.57028,
                31.569769,
                31.569256,
                31.568743,
                31.568228,
                31.567713,
                31.567196,
                31.566679,
                31.56616,
                31.565641,
                31.56512,
                31.5646,
                31.564077,
                31.563555,
                31.56303,
                31.562506,
                31.56198,
                31.561453,
                31.560925,
                31.560396,
                31.559868,
            ],
            [
                31.590534,
                31.590021,
                31.589508,
                31.588993,
                31.588478,
                31.587961,
                31.587444,
                31.586926,
                31.586405,
                31.585884,
                31.585361,
                31.584839,
                31.584314,
                31.58379,
                31.583263,
                31.582737,
                31.58221,
                31.581682,
                31.581152,
                31.580622,
                31.580091,
                31.57956,
                31.579025,
                31.578491,
                31.577955,
                31.57742,
                31.576883,
                31.576345,
                31.575808,
                31.575268,
                31.574726,
                31.574186,
            ],
            [
                31.605228,
                31.604704,
                31.60418,
                31.603651,
                31.603125,
                31.602594,
                31.602064,
                31.601534,
                31.601002,
                31.600468,
                31.599934,
                31.599398,
                31.598862,
                31.598324,
                31.597786,
                31.597248,
                31.596708,
                31.596167,
                31.595627,
                31.595083,
                31.59454,
                31.593996,
                31.59345,
                31.592905,
                31.592358,
                31.59181,
                31.59126,
                31.590712,
                31.59016,
                31.58961,
                31.589058,
                31.588505,
            ],
            [
                31.619925,
                31.619389,
                31.618849,
                31.61831,
                31.61777,
                31.617228,
                31.616686,
                31.616142,
                31.615597,
                31.615051,
                31.614506,
                31.613956,
                31.613409,
                31.61286,
                31.612309,
                31.611757,
                31.611206,
                31.610653,
                31.6101,
                31.609545,
                31.60899,
                31.608435,
                31.607876,
                31.607319,
                31.60676,
                31.6062,
                31.605639,
                31.605078,
                31.604515,
                31.603952,
                31.60339,
                31.602825,
            ],
            [
                31.634623,
                31.634071,
                31.633522,
                31.632969,
                31.632416,
                31.631863,
                31.631308,
                31.63075,
                31.630194,
                31.629637,
                31.629078,
                31.628517,
                31.627956,
                31.627394,
                31.626831,
                31.626268,
                31.625706,
                31.62514,
                31.624575,
                31.624008,
                31.62344,
                31.622871,
                31.622303,
                31.621733,
                31.621162,
                31.62059,
                31.620018,
                31.619446,
                31.618872,
                31.618296,
                31.617722,
                31.617144,
            ],
            [
                31.649319,
                31.648756,
                31.648193,
                31.647629,
                31.647062,
                31.646496,
                31.64593,
                31.64536,
                31.64479,
                31.64422,
                31.64365,
                31.643078,
                31.642504,
                31.64193,
                31.641356,
                31.64078,
                31.640203,
                31.639627,
                31.63905,
                31.638472,
                31.637892,
                31.637312,
                31.63673,
                31.636148,
                31.635565,
                31.634981,
                31.634398,
                31.633812,
                31.633226,
                31.63264,
                31.632053,
                31.631464,
            ],
            [
                31.664017,
                31.66344,
                31.662865,
                31.662289,
                31.66171,
                31.66113,
                31.660551,
                31.659971,
                31.65939,
                31.658806,
                31.658222,
                31.657639,
                31.657053,
                31.656467,
                31.65588,
                31.655293,
                31.654703,
                31.654114,
                31.653524,
                31.652935,
                31.652342,
                31.65175,
                31.651157,
                31.650564,
                31.64997,
                31.649374,
                31.648777,
                31.64818,
                31.647583,
                31.646984,
                31.646385,
                31.645786,
            ],
            [
                31.546421,
                31.546041,
                31.54566,
                31.545277,
                31.544891,
                31.544506,
                31.544119,
                31.54373,
                31.54334,
                31.542948,
                31.542555,
                31.542162,
                31.541765,
                31.541368,
                31.540972,
                31.540571,
                31.54017,
                31.539768,
                31.539366,
                31.53896,
                31.538553,
                31.538145,
                31.537737,
                31.537327,
                31.536917,
                31.536503,
                31.53609,
                31.535675,
                31.53526,
                31.534843,
                31.534424,
                31.534006,
            ],
            [
                31.561115,
                31.560722,
                31.560328,
                31.559933,
                31.559536,
                31.559137,
                31.558737,
                31.558336,
                31.557934,
                31.55753,
                31.557125,
                31.556719,
                31.55631,
                31.555902,
                31.555492,
                31.55508,
                31.554667,
                31.554253,
                31.553837,
                31.55342,
                31.553001,
                31.552582,
                31.55216,
                31.551739,
                31.551315,
                31.550892,
                31.550467,
                31.55004,
                31.549612,
                31.549183,
                31.548754,
                31.548323,
            ],
            [
                31.57581,
                31.575403,
                31.574997,
                31.574589,
                31.574179,
                31.573769,
                31.573357,
                31.572943,
                31.572529,
                31.572111,
                31.571693,
                31.571276,
                31.570856,
                31.570435,
                31.570011,
                31.569588,
                31.569164,
                31.568737,
                31.568308,
                31.567879,
                31.56745,
                31.567019,
                31.566586,
                31.56615,
                31.565716,
                31.565279,
                31.564842,
                31.564404,
                31.563965,
                31.563524,
                31.563084,
                31.56264,
            ],
            [
                31.590504,
                31.590086,
                31.589666,
                31.589245,
                31.588823,
                31.5884,
                31.587976,
                31.58755,
                31.587122,
                31.586693,
                31.586264,
                31.585833,
                31.585402,
                31.584967,
                31.584532,
                31.584097,
                31.58366,
                31.583221,
                31.58278,
                31.58234,
                31.581898,
                31.581453,
                31.581009,
                31.580563,
                31.580116,
                31.579668,
                31.57922,
                31.57877,
                31.578318,
                31.577866,
                31.577412,
                31.576958,
            ],
            [
                31.605198,
                31.604767,
                31.604336,
                31.603903,
                31.603468,
                31.603031,
                31.602594,
                31.602156,
                31.601717,
                31.601276,
                31.600834,
                31.600391,
                31.599945,
                31.5995,
                31.599052,
                31.598606,
                31.598156,
                31.597706,
                31.597254,
                31.5968,
                31.596346,
                31.59589,
                31.595434,
                31.594976,
                31.594517,
                31.594057,
                31.593596,
                31.593134,
                31.59267,
                31.592207,
                31.591742,
                31.591276,
            ],
            [
                31.619892,
                31.61945,
                31.619005,
                31.618559,
                31.618113,
                31.617664,
                31.617214,
                31.616764,
                31.616312,
                31.615858,
                31.615404,
                31.614948,
                31.61449,
                31.614033,
                31.613575,
                31.613113,
                31.612654,
                31.61219,
                31.611727,
                31.611261,
                31.610794,
                31.610327,
                31.609858,
                31.609388,
                31.608917,
                31.608446,
                31.607973,
                31.607498,
                31.607025,
                31.606548,
                31.606071,
                31.605595,
            ],
            [
                31.634588,
                31.634132,
                31.633675,
                31.633217,
                31.632757,
                31.632296,
                31.631834,
                31.63137,
                31.630907,
                31.630442,
                31.629974,
                31.629507,
                31.629038,
                31.628567,
                31.628096,
                31.627623,
                31.62715,
                31.626675,
                31.6262,
                31.625721,
                31.625242,
                31.624763,
                31.624283,
                31.623802,
                31.623318,
                31.622835,
                31.62235,
                31.621864,
                31.621378,
                31.62089,
                31.620401,
                31.619913,
            ],
            [
                31.649282,
                31.648815,
                31.648346,
                31.647875,
                31.647402,
                31.646929,
                31.646454,
                31.645979,
                31.645502,
                31.645023,
                31.644545,
                31.644064,
                31.643583,
                31.6431,
                31.642618,
                31.642134,
                31.641647,
                31.641161,
                31.640673,
                31.640182,
                31.639692,
                31.6392,
                31.638708,
                31.638214,
                31.63772,
                31.637224,
                31.636728,
                31.63623,
                31.635733,
                31.635233,
                31.634731,
                31.634232,
            ],
            [
                31.663979,
                31.663498,
                31.663015,
                31.662533,
                31.662048,
                31.661562,
                31.661076,
                31.660587,
                31.660097,
                31.659607,
                31.659117,
                31.658623,
                31.65813,
                31.657635,
                31.657139,
                31.656643,
                31.656145,
                31.655645,
                31.655146,
                31.654644,
                31.654142,
                31.653639,
                31.653133,
                31.652628,
                31.652122,
                31.651615,
                31.651106,
                31.650597,
                31.650085,
                31.649574,
                31.649063,
                31.64855,
            ],
            [
                31.678675,
                31.67818,
                31.677687,
                31.67719,
                31.676693,
                31.676195,
                31.675695,
                31.675196,
                31.674694,
                31.67419,
                31.673687,
                31.673182,
                31.672676,
                31.672169,
                31.671661,
                31.671152,
                31.670643,
                31.670132,
                31.669619,
                31.669106,
                31.66859,
                31.668076,
                31.667559,
                31.667042,
                31.666523,
                31.666004,
                31.665483,
                31.664963,
                31.66444,
                31.663918,
                31.663395,
                31.66287,
            ],
            [
                31.69337,
                31.692865,
                31.692358,
                31.691849,
                31.69134,
                31.690828,
                31.690317,
                31.689804,
                31.689291,
                31.688776,
                31.68826,
                31.687742,
                31.687223,
                31.686705,
                31.686184,
                31.685663,
                31.68514,
                31.684618,
                31.684093,
                31.683569,
                31.683043,
                31.682514,
                31.681986,
                31.681456,
                31.680925,
                31.680395,
                31.679863,
                31.679329,
                31.678797,
                31.67826,
                31.677725,
                31.677189,
            ],
            [
                31.708067,
                31.707548,
                31.70703,
                31.706509,
                31.705986,
                31.705463,
                31.704939,
                31.704412,
                31.703886,
                31.70336,
                31.702831,
                31.702301,
                31.70177,
                31.70124,
                31.700708,
                31.700174,
                31.69964,
                31.699104,
                31.698568,
                31.69803,
                31.697493,
                31.696953,
                31.696413,
                31.695871,
                31.69533,
                31.694786,
                31.694242,
                31.693697,
                31.693151,
                31.692604,
                31.692057,
                31.69151,
            ],
            [
                31.722765,
                31.722233,
                31.7217,
                31.721167,
                31.720633,
                31.720097,
                31.71956,
                31.719023,
                31.718485,
                31.717945,
                31.717403,
                31.716862,
                31.71632,
                31.715776,
                31.71523,
                31.714685,
                31.71414,
                31.713593,
                31.713043,
                31.712494,
                31.711943,
                31.711391,
                31.71084,
                31.710287,
                31.709732,
                31.709177,
                31.708622,
                31.708065,
                31.707508,
                31.70695,
                31.70639,
                31.70583,
            ],
            [
                31.737461,
                31.736917,
                31.736374,
                31.735826,
                31.735281,
                31.734732,
                31.734182,
                31.733633,
                31.733082,
                31.732529,
                31.731977,
                31.731422,
                31.730867,
                31.730312,
                31.729755,
                31.729197,
                31.72864,
                31.728079,
                31.727518,
                31.726957,
                31.726395,
                31.725832,
                31.725267,
                31.7247,
                31.724136,
                31.723568,
                31.723001,
                31.722433,
                31.721863,
                31.721294,
                31.720722,
                31.720152,
            ],
            [
                31.75216,
                31.751602,
                31.751045,
                31.750486,
                31.749928,
                31.749367,
                31.748806,
                31.748243,
                31.74768,
                31.747116,
                31.74655,
                31.745983,
                31.745417,
                31.744848,
                31.74428,
                31.74371,
                31.74314,
                31.742567,
                31.741995,
                31.74142,
                31.740847,
                31.74027,
                31.739695,
                31.739119,
                31.73854,
                31.73796,
                31.737381,
                31.736801,
                31.73622,
                31.735638,
                31.735056,
                31.734472,
            ],
            [
                31.766857,
                31.766289,
                31.765718,
                31.765148,
                31.764576,
                31.764004,
                31.76343,
                31.762854,
                31.762278,
                31.761702,
                31.761124,
                31.760546,
                31.759966,
                31.759386,
                31.758804,
                31.758223,
                31.757639,
                31.757055,
                31.756472,
                31.755886,
                31.755299,
                31.754711,
                31.754124,
                31.753534,
                31.752945,
                31.752354,
                31.751762,
                31.751171,
                31.750578,
                31.749985,
                31.74939,
                31.748795,
            ],
            [
                31.648563,
                31.64819,
                31.647814,
                31.647438,
                31.64706,
                31.64668,
                31.6463,
                31.645916,
                31.645533,
                31.645147,
                31.644762,
                31.644373,
                31.643984,
                31.643593,
                31.643202,
                31.642809,
                31.642414,
                31.642017,
                31.641619,
                31.64122,
                31.64082,
                31.640417,
                31.640015,
                31.63961,
                31.639204,
                31.638798,
                31.63839,
                31.637981,
                31.637571,
                31.63716,
                31.636747,
                31.636333,
            ],
            [
                31.663258,
                31.66287,
                31.662483,
                31.662094,
                31.661703,
                31.66131,
                31.660917,
                31.660522,
                31.660126,
                31.659729,
                31.65933,
                31.65893,
                31.658527,
                31.658125,
                31.65772,
                31.657316,
                31.65691,
                31.6565,
                31.65609,
                31.65568,
                31.655266,
                31.654852,
                31.654438,
                31.654022,
                31.653605,
                31.653185,
                31.652765,
                31.652346,
                31.651922,
                31.6515,
                31.651075,
                31.65065,
            ],
            [
                31.677952,
                31.677551,
                31.67715,
                31.67675,
                31.676346,
                31.675941,
                31.675537,
                31.675129,
                31.67472,
                31.67431,
                31.673899,
                31.673487,
                31.673073,
                31.672657,
                31.672241,
                31.671824,
                31.671404,
                31.670984,
                31.670563,
                31.67014,
                31.669714,
                31.669289,
                31.668861,
                31.668432,
                31.668003,
                31.667574,
                31.667141,
                31.666708,
                31.666275,
                31.66584,
                31.665403,
                31.664967,
            ],
            [
                31.692644,
                31.692234,
                31.69182,
                31.691406,
                31.69099,
                31.690573,
                31.690155,
                31.689735,
                31.689314,
                31.688892,
                31.688469,
                31.688044,
                31.687618,
                31.68719,
                31.686762,
                31.68633,
                31.6859,
                31.685467,
                31.685034,
                31.684599,
                31.684162,
                31.683723,
                31.683285,
                31.682844,
                31.682404,
                31.681961,
                31.681519,
                31.681072,
                31.680628,
                31.680182,
                31.679733,
                31.679285,
            ],
            [
                31.70734,
                31.706915,
                31.70649,
                31.706062,
                31.705635,
                31.705206,
                31.704775,
                31.704342,
                31.703909,
                31.703474,
                31.703037,
                31.7026,
                31.702162,
                31.701723,
                31.701283,
                31.70084,
                31.700397,
                31.699951,
                31.699505,
                31.699059,
                31.698608,
                31.69816,
                31.697708,
                31.697256,
                31.696804,
                31.69635,
                31.695894,
                31.695438,
                31.69498,
                31.69452,
                31.694061,
                31.693602,
            ],
            [
                31.722034,
                31.721598,
                31.721159,
                31.72072,
                31.72028,
                31.719837,
                31.719393,
                31.718948,
                31.718504,
                31.718056,
                31.717607,
                31.717157,
                31.716707,
                31.716255,
                31.715803,
                31.71535,
                31.714893,
                31.714436,
                31.713978,
                31.713518,
                31.713057,
                31.712595,
                31.712133,
                31.711668,
                31.711205,
                31.710737,
                31.71027,
                31.709803,
                31.709333,
                31.708862,
                31.708391,
                31.707918,
            ],
            [
                31.736729,
                31.736279,
                31.735828,
                31.735376,
                31.734924,
                31.734468,
                31.734013,
                31.733557,
                31.733097,
                31.73264,
                31.732178,
                31.731716,
                31.731253,
                31.73079,
                31.730324,
                31.729856,
                31.72939,
                31.72892,
                31.72845,
                31.727978,
                31.727507,
                31.727032,
                31.726557,
                31.726082,
                31.725605,
                31.725126,
                31.724648,
                31.724167,
                31.723686,
                31.723204,
                31.722721,
                31.722237,
            ],
            [
                31.751425,
                31.750961,
                31.750498,
                31.750034,
                31.749569,
                31.749102,
                31.748632,
                31.748163,
                31.747692,
                31.747221,
                31.746748,
                31.746273,
                31.745798,
                31.745321,
                31.744844,
                31.744366,
                31.743887,
                31.743404,
                31.742922,
                31.74244,
                31.741955,
                31.741468,
                31.740982,
                31.740494,
                31.740005,
                31.739515,
                31.739025,
                31.738533,
                31.738039,
                31.737545,
                31.737051,
                31.736555,
            ],
            [
                31.766119,
                31.765644,
                31.76517,
                31.764692,
                31.764214,
                31.763735,
                31.763254,
                31.762772,
                31.76229,
                31.761805,
                31.761318,
                31.760832,
                31.760345,
                31.759855,
                31.759367,
                31.758875,
                31.758383,
                31.75789,
                31.757395,
                31.7569,
                31.756403,
                31.755905,
                31.755407,
                31.754908,
                31.754406,
                31.753904,
                31.7534,
                31.752897,
                31.752394,
                31.751888,
                31.75138,
                31.750874,
            ],
            [
                31.780815,
                31.780329,
                31.779839,
                31.77935,
                31.778858,
                31.778368,
                31.777874,
                31.77738,
                31.776884,
                31.776388,
                31.77589,
                31.77539,
                31.77489,
                31.77439,
                31.773888,
                31.773384,
                31.77288,
                31.772375,
                31.77187,
                31.771362,
                31.770853,
                31.770344,
                31.769833,
                31.76932,
                31.768808,
                31.768293,
                31.767778,
                31.767263,
                31.766747,
                31.76623,
                31.76571,
                31.765192,
            ],
            [
                31.795511,
                31.795012,
                31.79451,
                31.794008,
                31.793505,
                31.793001,
                31.792494,
                31.791988,
                31.791481,
                31.790972,
                31.79046,
                31.78995,
                31.789438,
                31.788925,
                31.78841,
                31.787895,
                31.787378,
                31.786861,
                31.786343,
                31.785824,
                31.785303,
                31.78478,
                31.784258,
                31.783733,
                31.783209,
                31.782684,
                31.782158,
                31.78163,
                31.781101,
                31.780573,
                31.780043,
                31.779512,
            ],
            [
                31.810207,
                31.809694,
                31.809181,
                31.808666,
                31.808151,
                31.807634,
                31.807116,
                31.806597,
                31.806076,
                31.805555,
                31.805033,
                31.80451,
                31.803986,
                31.80346,
                31.802933,
                31.802406,
                31.801878,
                31.801348,
                31.800817,
                31.800285,
                31.799753,
                31.79922,
                31.798683,
                31.79815,
                31.797611,
                31.797073,
                31.796535,
                31.795998,
                31.795456,
                31.794916,
                31.794374,
                31.79383,
            ],
            [
                31.824903,
                31.824379,
                31.823853,
                31.823326,
                31.822798,
                31.822268,
                31.821737,
                31.821205,
                31.820673,
                31.820139,
                31.819605,
                31.819069,
                31.818533,
                31.817995,
                31.817455,
                31.816916,
                31.816376,
                31.815834,
                31.81529,
                31.814747,
                31.814203,
                31.813658,
                31.81311,
                31.812563,
                31.812014,
                31.811464,
                31.810915,
                31.810364,
                31.809813,
                31.80926,
                31.808706,
                31.808151,
            ],
            [
                31.839602,
                31.839064,
                31.838526,
                31.837986,
                31.837444,
                31.836903,
                31.836359,
                31.835815,
                31.83527,
                31.834724,
                31.834177,
                31.83363,
                31.83308,
                31.832531,
                31.83198,
                31.831427,
                31.830875,
                31.83032,
                31.829765,
                31.82921,
                31.828653,
                31.828096,
                31.827538,
                31.826979,
                31.826418,
                31.825857,
                31.825294,
                31.824732,
                31.824167,
                31.823603,
                31.823038,
                31.822472,
            ],
            [
                31.8543,
                31.853748,
                31.853197,
                31.852646,
                31.852093,
                31.851538,
                31.850983,
                31.850426,
                31.849869,
                31.84931,
                31.848751,
                31.84819,
                31.84763,
                31.847067,
                31.846504,
                31.84594,
                31.845375,
                31.844809,
                31.844242,
                31.843674,
                31.843105,
                31.842535,
                31.841965,
                31.841393,
                31.84082,
                31.840248,
                31.839674,
                31.8391,
                31.838524,
                31.837948,
                31.83737,
                31.836792,
            ],
            [
                31.868998,
                31.868435,
                31.86787,
                31.867306,
                31.86674,
                31.866173,
                31.865606,
                31.865036,
                31.864466,
                31.863895,
                31.863323,
                31.862751,
                31.862177,
                31.861603,
                31.861029,
                31.86045,
                31.859875,
                31.859297,
                31.858717,
                31.858137,
                31.857557,
                31.856976,
                31.856392,
                31.855808,
                31.855225,
                31.85464,
                31.854053,
                31.853468,
                31.85288,
                31.852293,
                31.851704,
                31.851114,
            ],
        ],
        dtype="float32",
    )

    # Interpolation variables
    tp_interpolation = n.createVariable("tp_interpolation", "i4", ())
    tp_interpolation.interpolation_name = "bi_quadratic_latitude_longitude"
    tp_interpolation.computational_precision = "32"
    tp_interpolation.tie_point_mapping = (
        "track: track_indices tie_point_track subarea_track "
        "scan: scan_indices tie_point_scan subarea_scan"
    )
    tp_interpolation.interpolation_parameters = (
        "ce1: ce1 ca2: ca2 ca3: ca3 "
        "interpolation_subarea_flags: interpolation_subarea_flags"
    )

    # Interpolation parameters
    ce1 = n.createVariable("ce1", "f4", ("tie_point_track", "subarea_scan"))
    ce1[...] = [
        [-0.00446631, -0.00456698],
        [-0.00446898, -0.00459249],
        [-0.00447288, -0.00457435],
        [-0.00448335, -0.0045854],
        [-0.00448197, -0.00459688],
        [-0.00445641, -0.00456489],
    ]
    ca1 = n.createVariable("ca1", "f4", ("tie_point_track", "subarea_scan"))
    ca1[...] = [
        [-4.6104342e-06, 6.1736027e-06],
        [-2.8001858e-07, 2.6631827e-07],
        [-1.7692255e-06, 5.2904676e-07],
        [1.6754498e-06, -5.7874269e-07],
        [4.3095083e-06, -1.1395372e-06],
        [1.3514027e-06, 3.5284631e-06],
    ]
    ce2 = n.createVariable("ce2", "f4", ("subarea_track", "tie_point_scan"))
    ce2[...] = [
        [1.0699123e-05, 1.4358953e-05, 1.3666599e-05],
        [9.6899485e-06, 3.3324793e-06, 6.9370931e-06],
        [5.7393891e-06, 1.0187923e-05, 8.7080189e-06],
        [7.8894655e-06, 1.6178783e-05, 1.1387640e-05],
    ]
    ca2 = n.createVariable("ca2", "f4", ("subarea_track", "tie_point_scan"))
    ca2[...] = (
        [
            [0.00127299, 0.00128059, 0.00123599],
            [0.00127416, 0.0013045, 0.00124623],
            [0.00127661, 0.00127138, 0.00122882],
            [0.0012689, 0.00129565, 0.00122312],
        ],
    )
    ce3 = n.createVariable("ce3", "f4", ("subarea_track", "subarea_scan"))
    ce3[...] = [
        [1.31605511e-05, 1.18703929e-05],
        [7.31968385e-06, 1.04031105e-05],
        [8.58208659e-06, 8.13388488e-06],
        [1.54361387e-05, 5.58498641e-06],
    ]
    ca3 = n.createVariable("ca3", "f4", ("subarea_track", "subarea_scan"))
    ca3[...] = [
        [0.00129351, 0.00123733],
        [0.00128829, 0.00123154],
        [0.0012818, 0.0012121],
        [0.00127719, 0.00122236],
    ]

    interpolation_subarea_flags = n.createVariable(
        "interpolation_subarea_flags", "i1", ("subarea_track", "subarea_scan")
    )
    interpolation_subarea_flags.flag_meanings = (
        "location_use_3d_cartesian "
        "sensor_direction_use_3d_cartesian "
        "solar_direction_use_3d_cartesian"
    )
    interpolation_subarea_flags.valid_range = np.array([0, 7], dtype="int8")
    interpolation_subarea_flags.flag_masks = np.array([1, 2, 4], dtype="int8")
    interpolation_subarea_flags[...] = [[0, 0], [0, 0], [0, 0], [0, 0]]

    # Data variables
    r = n.createVariable("r", "f4", ("track", "scan"))
    r.long_name = "radiance"
    r.units = "W m-2 sr-1"
    r.coordinate_interpolation = "lat: lon: tp_interpolation"
    r[...] = np.arange(48 * 32).reshape(48, 32)

    n.close()

    return filename


contiguous_file = _make_contiguous_file("DSG_timeSeries_contiguous.nc")
indexed_file = _make_indexed_file("DSG_timeSeries_indexed.nc")
indexed_contiguous_file = _make_indexed_contiguous_file(
    "DSG_timeSeriesProfile_indexed_contiguous.nc"
)

(
    parent_file,
    external_file,
    combined_file,
    external_missing_file,
) = _make_external_files()

geometry_1_file = _make_geometry_1_file("geometry_1.nc")
geometry_2_file = _make_geometry_2_file("geometry_2.nc")
geometry_3_file = _make_geometry_3_file("geometry_3.nc")
geometry_4_file = _make_geometry_4_file("geometry_4.nc")
interior_ring_file = _make_interior_ring_file("geometry_interior_ring.nc")
interior_ring_file_2 = _make_interior_ring_file_2(
    "geometry_interior_ring_2.nc"
)

gathered = _make_gathered_file("gathered.nc")

string_char_file = _make_string_char_file("string_char.nc")

broken_bounds_file = _make_broken_bounds_cdl("broken_bounds.cdl")

subsampled_file_1 = _make_subsampled_1("subsampled_1.nc")
subsampled_file_1 = _make_subsampled_2("subsampled_2.nc")


if __name__ == "__main__":
    print("Run date:", datetime.datetime.now())
    cf.environment()
    print()
    unittest.main(verbosity=2)
