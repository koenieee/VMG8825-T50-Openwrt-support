# ATENv3

Enabling unbricking of Zyxel devices such as `DX3301-T0`, `EX3301-T0`.

The algorithm is just a basic obfuscation of some per-device data.
We should thank Zyxel for keeping their devices accessible to the
open source community by not switching this to a per-unit password
burned in flash, or some kind of signature scheme where decoding
is impossible.

## Usage

```bash
cc -o atenv3_passwd ./atenv3_passwd.c

./atenv3_passwd 2400C00C09503316E000148493987B03118E
363943360710703246723488897955
```

## License
GPL-2.0