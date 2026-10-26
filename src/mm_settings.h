#ifndef MM_SETTINGS_H
#define MM_SETTINGS_H

#include <stdint.h>

/* Prepend file defaults, leaving explicit CLI options in control. */
int mm_config_arguments(int *argc, char ***argv, const char *options);
/* NULL means use the built-in terminal-type/ROM defaults. */
const uint8_t *mm_config_tables(const char *firmware);

#endif
