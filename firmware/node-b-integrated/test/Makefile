# Lõi C dạng thư viện dùng chung cho node giả của Pi (pi/src/nt532/net/fakenode.py) + test shim
CORE := ../main
CFLAGS += -std=c11 -Wall -Wextra -Werror -O1 -g -I$(CORE)
MINGW ?= x86_64-w64-mingw32-gcc
LIBSRC := core_shim.c $(CORE)/integrated_control.c $(CORE)/probe_state.c
DEPS := Makefile $(LIBSRC) $(wildcard $(CORE)/*.h)

.PHONY: test_shim clean
test_shim: test_shim_bin
	./test_shim_bin

test_shim_bin: $(DEPS) test_shim.c
	$(CC) $(CFLAGS) test_shim.c $(LIBSRC) -o $@

libintegrated.so: $(DEPS)
	$(CC) $(CFLAGS) -shared -fPIC $(LIBSRC) -o $@

integrated.dll: $(DEPS)
	$(MINGW) $(CFLAGS) -shared $(LIBSRC) -o $@

clean:
	rm -f libintegrated.so integrated.dll test_shim_bin
