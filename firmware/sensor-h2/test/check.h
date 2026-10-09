// Khung kiểm tra tối giản cho test C.
#pragma once

#include <stdio.h>
#include <string.h>

extern int g_checks, g_fails;

#define CHECK(c)                                                               \
    do {                                                                       \
        g_checks++;                                                            \
        if (!(c)) {                                                            \
            g_fails++;                                                         \
            fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #c);     \
        }                                                                      \
    } while (0)

#define CHECK_EQ(a, b)                                                         \
    do {                                                                       \
        long long a_ = (long long)(a), b_ = (long long)(b);                    \
        g_checks++;                                                            \
        if (a_ != b_) {                                                        \
            g_fails++;                                                         \
            fprintf(stderr, "%s:%d: %s == %s (%lld vs %lld)\n", __FILE__,      \
                    __LINE__, #a, #b, a_, b_);                                 \
        }                                                                      \
    } while (0)

// So chuỗi, NULL chỉ bằng NULL.
#define CHECK_STR(a, b)                                                        \
    do {                                                                       \
        const char *a_ = (a), *b_ = (b);                                       \
        g_checks++;                                                            \
        if ((a_ == NULL) != (b_ == NULL) || (a_ && strcmp(a_, b_) != 0)) {     \
            g_fails++;                                                         \
            fprintf(stderr, "%s:%d: %s == %s (\"%s\" vs \"%s\")\n", __FILE__,  \
                    __LINE__, #a, #b, a_ ? a_ : "(null)", b_ ? b_ : "(null)"); \
        }                                                                      \
    } while (0)

void test_actuator(void);
void test_alarm(void);
void test_proto(int verbose);
