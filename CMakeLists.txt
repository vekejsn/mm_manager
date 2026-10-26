# CMakeList.txt : CMake project for mm_manager
#
cmake_minimum_required (VERSION 3.25)

project ("mm_manager" LANGUAGES C)
# --- Versioning ---
find_package(Git QUIET)
set(FULL_GIT_VERSION "0.0.0-nogit")     # Default full descriptive version
set(CMAKE_PROJECT_VERSION "0.0.0")      # Default version for project() command

if(Git_FOUND AND EXISTS "${CMAKE_SOURCE_DIR}/.git")
    # 1. Get the full descriptive version (tags, hash, dirty status)
    execute_process(
        COMMAND ${GIT_EXECUTABLE} describe --tags --dirty --always
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        OUTPUT_VARIABLE _full_git_version_temp
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
        RESULT_VARIABLE GIT_DESCRIBE_RESULT
    )
    if(GIT_DESCRIBE_RESULT EQUAL 0)
        set(FULL_GIT_VERSION ${_full_git_version_temp})
        message(STATUS "Raw Git describe version: ${FULL_GIT_VERSION}")
    else()
        message(WARNING "git describe --tags --dirty --always failed. Using fallback version ${FULL_GIT_VERSION}.")
    endif()

    # 2. Try to get the latest tag and extract Major.Minor.Patch for project()
    execute_process(
        COMMAND ${GIT_EXECUTABLE} describe --tags --abbrev=0
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        OUTPUT_VARIABLE LATEST_TAG
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
        RESULT_VARIABLE GIT_TAG_RESULT
    )
    if(GIT_TAG_RESULT EQUAL 0)
        # Try to extract X.Y.Z or X.Y from tag (allowing optional 'v' prefix)
        string(REGEX MATCH "^v?([0-9]+\\.[0-9]+(\\.[0-9]+)?)" _extracted_version ${LATEST_TAG})
        if(CMAKE_MATCH_1)
            set(CMAKE_PROJECT_VERSION ${CMAKE_MATCH_1})
            message(STATUS "Using semantic version for CMake project: ${CMAKE_PROJECT_VERSION}")
        else()
            message(WARNING "Latest Git tag (${LATEST_TAG}) doesn't match expected format (X.Y or X.Y.Z). Using default ${CMAKE_PROJECT_VERSION} for project().")
        endif()
    else()
        message(WARNING "Could not get latest Git tag. Using default version ${CMAKE_PROJECT_VERSION} for project().")
    endif()

    if("${CMAKE_PROJECT_VERSION}" STREQUAL "${FULL_GIT_VERSION}")
        set(FULL_GIT_VERSION "OFFICIAL RELEASE")
        message(STATUS "Current commit matches latest tag. Setting version to OFFICIAL RELEASE.")
    endif()

else()
    message(WARNING "Git not found or not a Git repository. Using fallback versions.")
    # Defaults are already set above
endif()

# Project name and version (using sanitized, CMake-compatible version)
project(mm_manager VERSION ${CMAKE_PROJECT_VERSION})

# Add version definitions globally for all subsequent targets
add_compile_definitions(
    CMAKE_VERSION_STR="${CMAKE_PROJECT_VERSION}"
    GIT_VERSION_STR="${FULL_GIT_VERSION}"
)

set(ignoreMe "${CMAKE_ASM_COMPILER}")
set(ignoreMe "${CMAKE_ASM_COMPILER_LAUNCHER}")
set(ignoreMe "${CMAKE_CXX_COMPILER}")
set(ignoreMe "${CMAKE_CXX_FLAGS_RELEASE}")
set(ignoreMe "${CMAKE_EXPORT_NO_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_EXPORT_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_FIND_PACKAGE_NO_SYSTEM_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_FIND_ROOT_PATH_MODE_LIBRARY}")
set(ignoreMe "${CMAKE_FIND_USE_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY}")
set(ignoreMe "${CMAKE_MODULE_LINKER_FLAGS}")
set(ignoreMe "${CMAKE_SHARED_LINKER_FLAGS}")
set(ignoreMe "${DL_LIBRARY}")

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if (MSVC)
    # Treat warnings as errors
    add_compile_options(/WX)
else()
    # Set the C standard to C11
    set(CMAKE_C_STANDARD 11)
    set(CMAKE_C_STANDARD_REQUIRED ON)

    # Enforce additional checking
    add_compile_options(-Wall -Wextra -Werror)
#    add_compile_options(-pedantic)
endif()

execute_process(COMMAND git describe --dirty --always --tags
                OUTPUT_VARIABLE GIT_REV
                ERROR_QUIET)

if ("${GIT_REV}" STREQUAL "")
    set(GIT_REV "Unknown")
endif()

string(STRIP "${GIT_REV}" GIT_REV)
add_definitions(-DVERSION=\"${GIT_REV}\")

include_directories("third-party" ".")

ADD_LIBRARY(mm_util STATIC "src/mm_util.c")
ADD_LIBRARY(sqlite3 STATIC "third-party/sqlite3.c" "third-party/sqlite3.h")

if(MSVC)
ADD_LIBRARY(mm_serial STATIC "src/mm_serial_win32.c" "src/mm_serial.h" "third-party/getopt.c" "third-party/getopt.h")
else()
ADD_LIBRARY(mm_serial STATIC "src/mm_serial_posix.c" "src/mm_serial.h")
endif()

# Built executables.
set(MANAGER_SRC
    "src/mm_manager.c"
    "src/mm_manager.h"
    "src/mm_settings.c"
    "src/mm_settings.h"
    "src/mm_accounting.c"
    "src/mm_connection.c"
    "src/mm_dialin.c"
    "src/mm_tcp.c"
    "src/mm_tcp.h"
    "src/mm_modem.c"
    "src/mm_pcap.c"
    "src/mm_pcap.h"
    "src/mm_proto.c"
    "src/mm_serial.c"
    "src/mm_serial.h"
    "src/mm_config.c"
    "src/mm_tables.c"
    "src/mm_udp.c"
    "src/mm_udp.h"
    "src/mm_sqlite3.c"
)

set(DLOG2PCAP_SRC
    "src/mm_dlog2pcap.c"
    "src/mm_manager.h"
    "src/mm_pcap.c"
    "src/mm_pcap.h"
)

add_executable (mm_manager ${MANAGER_SRC})
#target_compile_options(mm_manager PUBLIC $<$<CONFIG:DEBUG>:-fprofile-instr-generate -fcoverage-mapping>)
if(MSVC)
TARGET_LINK_LIBRARIES(mm_manager mm_serial mm_util sqlite3 wsock32 ws2_32)
else()
TARGET_LINK_LIBRARIES(mm_manager mm_serial mm_util sqlite3 pthread dl)
endif()
add_executable (mm_admess "src/mm_admess.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_admess mm_util)
add_executable (mm_areacode "src/mm_areacode.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_areacode mm_util)
add_executable (mm_callscrn "src/mm_callscrn.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_callscrn mm_util)
add_executable (mm_convert_callscrn_mtr2_to_mtr1 "src/mm_convert_callscrn_mtr2_to_mtr1.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_convert_callscrn_mtr2_to_mtr1 mm_util)
add_executable (mm_card "src/mm_card.c" "src/mm_manager.h" "src/mm_card.h")
TARGET_LINK_LIBRARIES(mm_card mm_util)
add_executable (mm_callin "src/mm_callin.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_callin mm_util)
add_executable (mm_callstat "src/mm_callstat.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_callstat mm_util)
add_executable (mm_card_mtr1 "src/mm_card_mtr1.c" "src/mm_manager.h" "src/mm_card.h")
TARGET_LINK_LIBRARIES(mm_card_mtr1 mm_util)
add_executable (mm_convert_card_mtr2_to_mtr1 "src/mm_convert_card_mtr2_to_mtr1.c" "src/mm_manager.h" "src/mm_card.h")
TARGET_LINK_LIBRARIES(mm_convert_card_mtr2_to_mtr1 mm_util)
add_executable (mm_carrier "src/mm_carrier.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_carrier mm_util)
add_executable (mm_carrier_mtr1 "src/mm_carrier_mtr1.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_carrier_mtr1 mm_util)
add_executable (mm_commstat "src/mm_commstat.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_commstat mm_util)
add_executable (mm_coinvl "src/mm_coinvl.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_coinvl mm_util)
add_executable (mm_fconfig "src/mm_fconfig.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_fconfig mm_util)
add_executable (mm_instsv "src/mm_instsv.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_instsv mm_util)
add_executable (mm_lcd "src/mm_lcd.c" "src/mm_manager.h")
add_executable (mm_limserv "src/mm_limserv.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_limserv mm_util)
add_executable (mm_luhn "src/mm_luhn.c")
add_executable (mm_packtest "src/mm_packtest.c")
add_executable (mm_rate "src/mm_rate.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_rate mm_util)
add_executable (mm_rateint "src/mm_rateint.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_rateint mm_util)
add_executable (mm_rdlist "src/mm_rdlist.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_rdlist mm_util)
add_executable (mm_smcard "src/mm_smcard.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_smcard mm_util)
add_executable (mm_table_cutter "src/mm_table_cutter.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_table_cutter mm_util)
add_executable (mm_userif "src/mm_userif.c" "src/mm_manager.h")
TARGET_LINK_LIBRARIES(mm_userif mm_util)
add_executable (mm_dlog2pcap ${DLOG2PCAP_SRC})
TARGET_LINK_LIBRARIES(mm_dlog2pcap mm_util)

if(MSVC)
  add_definitions(-D_CRT_SECURE_NO_DEPRECATE)
  target_link_libraries(mm_carrier wsock32 ws2_32 sqlite3)
  target_link_libraries(mm_carrier_mtr1 wsock32 ws2_32 sqlite3)
  target_link_libraries(mm_smcard wsock32 ws2_32 sqlite3)
  endif()

# Install
set(INSTALL_TARGETS
    "mm_manager"
    "mm_admess"
    "mm_areacode"
    "mm_callin"
    "mm_callscrn"
    "mm_callstat"
    "mm_card"
    "mm_card_mtr1"
    "mm_carrier"
    "mm_carrier_mtr1"
    "mm_convert_callscrn_mtr2_to_mtr1"
    "mm_convert_card_mtr2_to_mtr1"
    "mm_coinvl"
    "mm_commstat"
    "mm_dlog2pcap"
    "mm_fconfig"
    "mm_instsv"
    "mm_lcd"
    "mm_limserv"
    "mm_luhn"
    "mm_rate"
    "mm_rateint"
    "mm_rdlist"
    "mm_smcard"
    "mm_table_cutter"
    "mm_userif"
)

install(TARGETS ${INSTALL_TARGETS} DESTINATION bin)
install(DIRECTORY wireshark DESTINATION .)
install(DIRECTORY config DESTINATION share/mm_manager/config)
install(DIRECTORY tables/default DESTINATION share/mm_manager/tables)
install(DIRECTORY tables/card_only DESTINATION share/mm_manager/tables)
install(DIRECTORY tables/coin DESTINATION share/mm_manager/tables)
install(DIRECTORY tables/desk DESTINATION share/mm_manager/tables)
install(FILES LICENSE DESTINATION .)
install(FILES README.md DESTINATION .)
install(FILES generate_intl_sbr.py generate_lcd.py generate_lcd_lata.py generate_lcd_lir.py generate_npa.py DESTINATION bin)

# --- CPack configuration ---

# Set basic package information
set(CPACK_PACKAGE_NAME "${PROJECT_NAME}")
set(CPACK_PACKAGE_VENDOR "Howard M. Harte")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Millennium Manager")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_VERSION_MAJOR "${PROJECT_VERSION_MAJOR}")
set(CPACK_PACKAGE_VERSION_MINOR "${PROJECT_VERSION_MINOR}")
set(CPACK_PACKAGE_VERSION_PATCH "${PROJECT_VERSION_PATCH}")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
set(CPACK_RESOURCE_FILE_README "${CMAKE_CURRENT_SOURCE_DIR}/README.md")

if(WIN32)
  set(CPACK_GENERATOR "ZIP")
else()
  set(CPACK_GENERATOR "TGZ")
endif()

include(CPack)

include(CTest)
if(BUILD_TESTING)
    add_executable(mm_dialin_router_test
        "src/dialin/test_router.c" "src/mm_dialin.c" "src/mm_serial.c" "src/mm_tcp.c")
    target_include_directories(mm_dialin_router_test PRIVATE "src")
    if(WIN32)
        target_link_libraries(mm_dialin_router_test ws2_32)
    endif()
    add_test(NAME dialin_router COMMAND mm_dialin_router_test)
    find_package(Python3 COMPONENTS Interpreter QUIET)
    if(Python3_Interpreter_FOUND)
        add_executable(mm_config_probe "tests/config_probe.c" "src/mm_settings.c")
        target_include_directories(mm_config_probe PRIVATE "src")
        add_test(NAME configuration
            COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_config.py"
                $<TARGET_FILE:mm_config_probe>)
        add_test(NAME dialin_controller
            COMMAND ${Python3_EXECUTABLE} -m unittest discover
                -s "${CMAKE_CURRENT_SOURCE_DIR}/src/dialin" -p "test_*.py")
    endif()
endif()
