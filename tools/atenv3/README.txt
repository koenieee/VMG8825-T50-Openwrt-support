# ATENv3 descrambler (source: https://github.com/cjdelisle/ATENv3)
build: cc -O2 -o atenv3_passwd atenv3_passwd.c

Example seed/password pair (VMG8825-T50), for testing the tool builds and
runs correctly -- not usable against a real device, since the seed (and
therefore the derived password) is different every session:
  seed     = 2E01C00309E01B14300B07B36A59F271010A
  password = 97760452113818812721555971724

Usage: ./atenv3_passwd <36-hex-seed>
