#include <string.h>

#include "check.h"

int g_checks, g_fails;

int main(int argc, char **argv)
{
    int verbose = argc > 1 && strcmp(argv[1], "-v") == 0;
    test_actuator();
    test_alarm();
    test_proto(verbose);
    if (g_fails) {
        fprintf(stderr, "FAIL: %d/%d\n", g_fails, g_checks);
        return 1;
    }
    if (verbose) {
        fprintf(stderr, "OK: %d checks\n", g_checks);
    }
    return 0;
}
