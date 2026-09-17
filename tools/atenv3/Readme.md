# ATENv3

Source: https://github.com/cjdelisle/ATENv3

Enabling unbricking of Zyxel devices such as `DX3301-T0`, `EX3301-T0`, and
(as used by this project) the `VMG8825-T50`.

The algorithm is just a basic obfuscation of some per-device data.
We should thank Zyxel for keeping their devices accessible to the
open source community by not switching this to a per-unit password
burned in flash, or some kind of signature scheme where decoding
is impossible.

## Build

```bash
cc -O2 -o atenv3_passwd atenv3_passwd.c
```

## Usage

```bash
./atenv3_passwd <36-hex-seed>
```

Example seed/password pair (VMG8825-T50), for testing the tool builds and
runs correctly — **not** usable against a real device, since the seed
(and therefore the derived password) is different every session:
```
seed     = 2E01C00309E01B14300B07B36A59F271010A
password = 97760452113818812721555971724
```

## License

GPL-2.0
