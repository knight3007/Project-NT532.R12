# Test C thuần trên máy: make -C firmware/sensor-h2/test
# make có sẵn CC=cc nên phải đè khi chưa đặt tay; cc mặc định trên máy này là clang Termux hỏng
ifeq ($(origin CC),default)
CC := /usr/bin/gcc
endif
CORE := ../main/core
CFLAGS += -std=c11 -Wall -Wextra -Werror -Wshadow -Wconversion -O1 -g -I$(CORE)
# ASan không chạy được trong PRoot (không mmap được shadow memory); chỉ dùng UBSan. Máy thường: make SAN='-fsanitize=address,undefined'
SAN ?= -fsanitize=undefined -fno-sanitize-recover=undefined
SRCS := test_main.c test_actuator.c test_alarm.c test_proto.c $(CORE)/actuator.c $(CORE)/alarm.c $(CORE)/proto.c

.PHONY: all test clean
all test: test_run
	./test_run

test_run: Makefile $(SRCS) check.h $(wildcard $(CORE)/*.h)
	$(CC) $(CFLAGS) $(SAN) $(SRCS) -o $@ -lm

clean:
	rm -f test_run
