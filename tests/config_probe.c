#include <stdio.h>
#include "mm_settings.h"

int main(int argc, char **argv) {
    const char *editions[] = {"06CNB02", "12AAV08", "UNKNOWN"};
    size_t i;
    if (mm_config_arguments(&argc, &argv, "a:b:cd:e:E:f:hi:j:k:l:mn:p:qrst:T:uvw") != 0) return 1;
    for (int arg = 1; arg < argc; arg++) puts(argv[arg]);
    for (i = 0; i < sizeof(editions) / sizeof(editions[0]); i++) {
        const uint8_t *tables = mm_config_tables(editions[i]);
        printf("%s:", editions[i]);
        if (!tables) printf("default");
        else for (; *tables; tables++) printf(" %u", *tables);
        putchar('\n');
    }
    return 0;
}
