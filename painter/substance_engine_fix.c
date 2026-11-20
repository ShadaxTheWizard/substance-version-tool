/* Preloaded into Substance 3D Painter 12.0.1 (Linux) by the substance-painter launcher.

   Painter's CPU Substance engine, libsubstance_sse2_blend.so, has functions whose 16-byte
   stack slots sit 8 bytes off alignment, so their aligned SSE moves (movaps) fault and
   Painter crashes (SIGSEGV on a "SubstanceThread").  It happens as soon as such a function
   runs, e.g. when a fill's properties show a preview of the fabric_felt material, and it
   takes any project using those substances down, whether converted or not.  Adobe has
   confirmed the bug (community.adobe.com bug report 1554909).

   The library is a direct dependency of the Painter executable, so it is mapped before this
   constructor runs.  Every movaps with a stack-relative operand is turned into movups (same
   length, no alignment requirement) in memory; the file on disk is not touched.  The list of
   sites (substance_engine_fix_12.0.1.h, from dev/gen_substance_engine_fix.py) belongs to one
   build: the library must have its size and the expected opcode at every site, or nothing
   is changed. */
#define _GNU_SOURCE
#include <link.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "substance_engine_fix_12.0.1.h"

static const char LIB[] = "libsubstance_sse2_blend.so";

static int find_lib(struct dl_phdr_info *info, size_t size, void *data)
{
    (void)size;
    size_t n = strlen(info->dlpi_name), k = sizeof LIB - 1;
    if (n >= k && strcmp(info->dlpi_name + n - k, LIB) == 0) {
        *(struct dl_phdr_info *)data = *info;
        return 1;
    }
    return 0;
}

__attribute__((constructor)) static void substance_engine_fix(void)
{
    struct dl_phdr_info info;
    if (!dl_iterate_phdr(find_lib, &info))
        return;
    struct stat st;
    if (stat(info.dlpi_name, &st) != 0 || st.st_size != SBS_LIB_SIZE) {
        fprintf(stderr, "substance_engine_fix: %s is not the known 12.0.1 build; not patched\n",
                info.dlpi_name);
        return;
    }
    unsigned char *base = (unsigned char *)info.dlpi_addr;
    int todo = 0;
    for (int i = 0; i < SBS_SITES; i++) {
        unsigned char b = base[sbs_site[i]];
        if (b == sbs_op[i])
            todo++;
        else if (b != sbs_op[i] - 0x18) {          /* neither movaps nor movups */
            fprintf(stderr, "substance_engine_fix: unexpected code at %#x; not patched\n",
                    sbs_site[i]);
            return;
        }
    }
    if (!todo)
        return;
    long page = sysconf(_SC_PAGESIZE);
    unsigned long lo = sbs_site[0] & ~(unsigned long)(page - 1);
    unsigned long hi = (sbs_site[SBS_SITES - 1] + page) & ~(unsigned long)(page - 1);
    if (mprotect(base + lo, hi - lo, PROT_READ | PROT_WRITE) != 0) {
        perror("substance_engine_fix: mprotect");
        return;
    }
    for (int i = 0; i < SBS_SITES; i++)
        base[sbs_site[i]] = sbs_op[i] - 0x18;      /* 0f 28 -> 0f 10, 0f 29 -> 0f 11 */
    if (mprotect(base + lo, hi - lo, PROT_READ | PROT_EXEC) != 0)
        perror("substance_engine_fix: mprotect");
}
