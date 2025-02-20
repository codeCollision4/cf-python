import datetime
import faulthandler
import json
import os
import stat
import subprocess
import unittest

import netCDF4

faulthandler.enable()  # to debug seg faults and timeouts

import cf


class cfaTest(unittest.TestCase):
    def setUp(self):
        self.test_file = "cfa_test.sh"
        self.test_path = os.path.join(os.getcwd(), self.test_file)

        # Need ./cfa_test.sh to be made executable to run it. Locally that
        # may already be true but for testing dists etc. must chmod here:
        os.chmod(
            self.test_path,
            stat.S_IRUSR
            | stat.S_IROTH
            | stat.S_IRGRP
            | stat.S_IXUSR  # reading
            | stat.S_IXOTH
            | stat.S_IXGRP  # executing
            | stat.S_IWUSR,  # writing
        )

    def test_cfa(self):
        # In the script, STDERR from cfa commands is redirected to
        # (overwrite) its STDOUT, so Popen's stdout is really the cfa
        # commands' stderr:
        cfa_test = subprocess.Popen(
            ["./" + self.test_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        cfa_stderr_via_stdout_channel, _ = cfa_test.communicate("yes\n")
        returncode = cfa_test.returncode
        if returncode != 0:
            self.fail(
                f"A cfa command failed (see script's 'exit {returncode}' "
                "point) with error:\n"
                f"{cfa_stderr_via_stdout_channel.decode('utf-8')}"
            )
        # else: (passes by default)

    def test_cfa_base(self):
        # Test the "base" cfa_option to cf.write. Valid for CFA-0.4.
        filename = "test_file.nc"
        f = cf.read(filename)
        cfa_file = "test_file.nca"
        cf.write(f, cfa_file, fmt="CFA4", cfa_options={"base": ""})

        nc = netCDF4.Dataset(cfa_file, "r")
        cfa_array = json.loads(nc.variables["eastward_wind"].cfa_array)

        self.assertEqual(cfa_array["base"], "")
        self.assertEqual(
            cfa_array["Partitions"][0]["subarray"]["file"], filename
        )

        os.remove(cfa_file)


if __name__ == "__main__":
    print("Run date:", datetime.datetime.now())
    cf.environment()
    print()
    unittest.main(verbosity=2)
