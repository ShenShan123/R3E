/*
 * VCS O-2018.09 references the legacy, unversioned pthread_yield symbol.
 * Modern glibc exports only pthread_yield@GLIBC_2.2.5, so provide the exact
 * ABI-compatible forwarding symbol without changing simulator semantics.
 */
#include <sched.h>

int pthread_yield(void) {
    return sched_yield();
}
