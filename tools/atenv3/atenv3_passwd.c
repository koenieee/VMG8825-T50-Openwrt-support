// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2025 Caleb James DeLisle
 *
 * This is a tool for deriving the bootloader unlock password
 * from the ATSE output in Zyxel 3.x devices. This is the version
 * with the 36 character ATSE.
 */
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

typedef struct {
	char field0[13];
	char field1[14];
	char field3[3];
	char field4[3];
	char field5[3];
	char field6[7];
} device_data_t;

void make_key(device_data_t *dd, char* out)
{
	uint32_t uVar3;
	{
		uint8_t buf[12] = {0};

		for (int i = 0; i < 4; i++)
			buf[i] = dd->field1[1 + i];

		for (int i = 0; i < 4; i++)
			buf[i + 4] = dd->field0[i];

		uVar3 = strtol((char *)buf, NULL, 16);
	}
	

	uint32_t uVar4;
	{
		uint8_t buf[12] = {0};

		for (int i = 0; i < 4; i++)
			buf[i] = dd->field1[5 + i];

		for (int i = 0; i < 4; i++)
			buf[i + 4] = dd->field0[4 + i];

		uVar4 = strtol((char *)buf, NULL, 16);
	}

	uint32_t uVar5;
	{
		uint8_t buf[16] = {0};
		for (int i = 0; i < 4; i++)
			buf[i] = dd->field1[9 + i];

		for (int i = 0; i < 4; i++)
			buf[4 + i] = dd->field0[8 + i];

		uVar5 = strtol((char *)buf, NULL, 16);
	}

	// Missing: field0[12], field1[0], field1[13]

	uint32_t uVar7;
	uint32_t uVar12;
	{
		int32_t iVar2 = strtol(dd->field3,NULL, 16);
		int32_t iVar10 = strtol(dd->field4,NULL, 16);
		int32_t iVar6 = strtol(dd->field5,NULL, 16);
		uVar7 = strtol(dd->field6,NULL, 16);
		uVar12 = iVar10 + iVar2 + iVar6;
	}

	const int magic[3] = { 0x10f0a563, 0xbeafbeaf, 0x14387052 };
	uint32_t uVar11 = (uVar7 & 0xffffff) + magic[uVar12 % 3];
	uVar12 = uVar11 >> (uVar12 & 3) & 0xff;

	uVar7 = 0;
	while (uVar7 != uVar12) {
		uVar3 = (uVar3 >> 1) + uVar3 * -0x80000000;
		uVar4 = (uVar4 >> 2) + uVar4 * 0x40000000;
		uVar7 = uVar7 + 1;
		uVar5 = (uVar5 >> 3) + uVar5 * 0x20000000;
	}
	snprintf(out, 31, "%u%u%u",
		 uVar3 ^ uVar11,
		 uVar4 ^ uVar11,
		 uVar5 ^ uVar11);
}

void atse_to_dd(device_data_t *dd, const uint8_t* atse)
{
	memset(dd, 0, sizeof *dd );
	for (int i = 0; i < 12; i++)
		dd->field0[i] = atse[i * 3 + 1];

	/* Field 1 byte 0 is not encoded in the atse, nor used in aten. */
	dd->field1[0] = 'N';

	for (int i = 0; i < 13; i++)
		dd->field1[i + 1] = atse[i * 3 + 0];

	dd->field3[0] = atse[2];
	dd->field3[1] = atse[5];
	dd->field4[0] = atse[8];
	dd->field4[1] = atse[11];
	dd->field5[0] = atse[14];
	dd->field5[1] = atse[17];

	for (int i = 0; i < 6; i++)
		dd->field6[i] = atse[20 + i * 3];
}

int main(int argc, char **argv) {
	if (argc < 2) {
		fprintf(stderr, "usage: %s <atse-hex-36chars>\n", argv[0]);
		return 2;
	}

	const char *atse = argv[1];

	if (strlen(atse) != 36) {
		fprintf(stderr, "Input is %zu characters; expected 36 hex chars. "
			"Probably wrong version.\n", strlen(atse));
		return -3;
	}

	device_data_t dd;
	atse_to_dd(&dd, (uint8_t *)atse);

	char key[31] = {0};
	make_key(&dd, key);
	printf("%s\n", key);

	return 0;
}