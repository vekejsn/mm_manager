#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mm_settings.h"

#define MAX_SETTINGS 64
#define MAX_VALUE 4096
#define END_DATA 13

static struct setting {
    const char *name;
    char option;
    int flag;
    char value[MAX_VALUE];
} settings[] = {
    {"MM_ACCESS_CODE", 'a', 0, ""},
    {"MM_BAUD_RATE", 'b', 0, ""},
    {"MM_COMPLETE_DOWNLOAD", 'c', 1, ""},
    {"MM_DEFAULT_TABLE_DIR", 'd', 0, ""},
    {"MM_ERROR_INJECT", 'e', 0, ""},
    {"MM_DEVICE", 'f', 0, ""},
    {"MM_MODEM_INIT", 'i', 0, ""},
    {"MM_DIALIN_PORT", 'j', 0, ""},
    {"MM_KEY_CARD_NUMBER", 'k', 0, ""},
    {"MM_LOG_FILE", 'l', 0, ""},
    {"MM_USE_MODEM", 'm', 1, ""},
    {"MM_NCC_NUMBER", 'n', 0, ""},
    {"MM_SECONDARY_NCC_NUMBER", 'n', 0, ""},
    {"MM_PCAP_FILE", 'p', 0, ""},
    {"MM_QUIET", 'q', 1, ""},
    {"MM_RATING_TEST", 'r', 1, ""},
    {"MM_MINIMAL_TABLES", 's', 1, ""},
    {"MM_TERM_TABLE_DIR", 't', 0, ""},
    {"MM_TCP_ENDPOINT", 'T', 0, ""},
    {"MM_SEND_UDP", 'u', 1, ""},
    {"MM_VERBOSE", 'v', 2, ""},
    {"MM_IGNORE_CARRIER", 'w', 1, ""},
};
static struct profile {
    char firmware[8];
    uint8_t tables[256];
} profiles[MAX_SETTINGS];
static size_t profile_count;
static char **arguments;
static char option_strings[sizeof(settings) / sizeof(settings[0])][12];

static const char *setting_value(const char *name) {
    size_t i;
    for (i = 0; i < sizeof(settings) / sizeof(settings[0]); i++) {
        if (strcmp(settings[i].name, name) == 0) return settings[i].value;
    }
    return "";
}

static void cleanup(void) {
    free(arguments);
}

static char *trim(char *text) {
    char *end;
    while (isspace((unsigned char)*text)) text++;
    end = text + strlen(text);
    while (end > text && isspace((unsigned char)end[-1])) *--end = '\0';
    return text;
}

static int parse_tables(char *value, uint8_t *tables) {
    size_t count = 0;
    unsigned char seen[256] = {0};
    char *cursor = value;
    while (*cursor) {
        char *end;
        long id;
        while (isspace((unsigned char)*cursor)) cursor++;
        errno = 0;
        id = strtol(cursor, &end, cursor[0] == '0' &&
            (cursor[1] == 'x' || cursor[1] == 'X') ? 16 : 10);
        if (cursor == end || errno || id < 1 || id > 255 || id == END_DATA || seen[id]) return -1;
        seen[id] = 1;
        tables[count++] = (uint8_t)id;
        cursor = end;
        while (isspace((unsigned char)*cursor)) cursor++;
        if (!*cursor) break;
        if (*cursor++ != ',' || !*trim(cursor)) return -1;
    }
    if (!count) return -1;
    tables[count++] = END_DATA;
    tables[count] = 0;
    return 0;
}

static int store(char *name, char *value) {
    size_t i;
    if (strncmp(name, "MM_TABLES_", 10) == 0) {
        struct profile *profile;
        if (strlen(name + 10) != 7) return -1;
        for (i = 10; name[i]; i++) {
            if (!isalnum((unsigned char)name[i])) return -1;
        }
        for (i = 0; i < profile_count; i++) {
            if (strcmp(profiles[i].firmware, name + 10) == 0) break;
        }
        if (i == MAX_SETTINGS) return -1;
        profile = &profiles[i];
        if (!*value) {
            /* An empty value explicitly restores the default. */
            if (i < profile_count) profiles[i] = profiles[--profile_count];
            return 0;
        }
        if (parse_tables(value, profile->tables) != 0) return -1;
        strcpy(profile->firmware, name + 10);
        if (i == profile_count) profile_count++;
        return 0;
    }
    for (i = 0; i < sizeof(settings) / sizeof(settings[0]); i++) {
        if (strcmp(name, settings[i].name) == 0) {
            if (*value && settings[i].flag) {
                if (settings[i].flag == 1 && strcmp(value, "true") == 0) value = "1";
                if (settings[i].flag == 1 && strcmp(value, "false") == 0) value = "0";
                if (strlen(value) != 1 || value[0] < '0' ||
                    value[0] > (settings[i].flag == 1 ? '1' : '9')) return -1;
            }
            strcpy(settings[i].value, value);
            return 0;
        }
    }
    /* Permit shared dotenv files; catch typos in this application's keys. */
    return strncmp(name, "MM_", 3) == 0 ? -1 : 0;
}

static int load(const char *path, int required) {
    char line[MAX_VALUE];
    unsigned int number = 0;
    FILE *file = fopen(path, "r");
    if (!file) {
        if (!required && errno == ENOENT) return 0;
        fprintf(stderr, "Cannot read configuration '%s': %s\n", path, strerror(errno));
        return -1;
    }
    while (fgets(line, sizeof(line), file)) {
        char *name, *value, *separator, *end;
        number++;
        if (!strchr(line, '\n') && !feof(file)) goto invalid;
        name = trim(line);
        if (!*name || *name == '#') continue;
        if (strncmp(name, "export ", 7) == 0) name = trim(name + 7);
        separator = strchr(name, '=');
        if (!separator) goto invalid;
        *separator = '\0';
        name = trim(name);
        value = trim(separator + 1);
        if (*value == '\'' || *value == '"') {
            char quote = *value++;
            end = strchr(value, quote);
            if (!end) goto invalid;
            *end++ = '\0';
            end = trim(end);
            if (*end && *end != '#') goto invalid;
        } else {
            for (end = value; *end; end++) {
                if (*end == '#' && (end == value || isspace((unsigned char)end[-1]))) {
                    *end = '\0';
                    break;
                }
            }
            value = trim(value);
        }
        if (!*name || store(name, value) != 0) goto invalid;
    }
    if (ferror(file)) goto invalid;
    fclose(file);
    return 0;
invalid:
    fprintf(stderr, "Invalid configuration in '%s' at line %u.\n", path, number);
    fclose(file);
    return -1;
}

int mm_config_arguments(int *argc, char ***argv, const char *options) {
    unsigned char explicit_options[256] = {0};
    const char *path = ".env";
    int required = 0, i, count = 1;
    size_t index;
    /* Inspect clustered options and skip their arguments, just as getopt does. */
    for (i = 1; i < *argc; i++) {
        char *cursor = (*argv)[i];
        if (strcmp(cursor, "--") == 0) break;
        if (*cursor++ != '-') continue;
        while (*cursor) {
            unsigned char option = (unsigned char)*cursor++;
            const char *spec = strchr(options, option);
            explicit_options[option] = 1;
            if (spec && spec[1] == ':') {
                const char *value = *cursor ? cursor : (i + 1 < *argc ? (*argv)[++i] : NULL);
                if (option == 'E' && value) { path = value; required = 1; }
                break;
            }
        }
    }
    if (load(path, required) != 0) return -1;
    if (!explicit_options['n'] && !*setting_value("MM_NCC_NUMBER") &&
        *setting_value("MM_SECONDARY_NCC_NUMBER")) {
        fprintf(stderr, "MM_SECONDARY_NCC_NUMBER requires MM_NCC_NUMBER.\n");
        return -1;
    }
    arguments = calloc((size_t)*argc + 2 * sizeof(settings) / sizeof(settings[0]) + 1, sizeof(char *));
    if (!arguments) return -1;
    atexit(cleanup);
    arguments[0] = (*argv)[0];
    for (index = 0; index < sizeof(settings) / sizeof(settings[0]); index++) {
        struct setting *setting = &settings[index];
        char *option = option_strings[index];
        if (!*setting->value || explicit_options[(unsigned char)setting->option]) continue;
        option[0] = '-';
        if (setting->flag) {
            int repeats = setting->value[0] - '0';
            if (!repeats) continue;
            memset(option + 1, setting->option, (size_t)repeats);
        } else {
            option[1] = setting->option;
        }
        arguments[count++] = option;
        if (!setting->flag) arguments[count++] = setting->value;
    }
    for (i = 1; i < *argc; i++) arguments[count++] = (*argv)[i];
    *argc = count;
    *argv = arguments;
    return 0;
}

const uint8_t *mm_config_tables(const char *firmware) {
    size_t i;
    for (i = 0; i < profile_count; i++) {
        if (strcmp(profiles[i].firmware, firmware) == 0) return profiles[i].tables;
    }
    return NULL;
}
