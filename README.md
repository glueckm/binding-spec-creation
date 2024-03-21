Generate a json file containg the widgets, structs, global API functions, enums, unions, types, ... of LVGL.
The purpose of this json file should be to contain all required information to be able to generate binding code for higher level functions (e.g.: micropython, cpython, rust, C++ ...)

This tool requires python >3.10 

Usage:
```
usage: parse_lvgl_files.py [-h] [--gcc GCC] [--fake-include FAKE_INCLUDE] [-c CONFIG] [-b LVGL_BASE]
                           [-s START_HEADER_FILE] [--load-parents LOAD_PARENTS]
                           cache

positional arguments:
  cache                 Filename for the resulting json file

options:
  -h, --help            show this help message and exit
  --gcc GCC             Executable which should be used for preprocessing the files before sent to the pycparser.
  --fake-include FAKE_INCLUDE
                        A path to a directory containing fake include files for the system include files normally
                        provided by teh compiler. Fake includes files a required because pycparser cannot parse the
                        original system includes
  -c CONFIG, --config CONFIG
                        Path to a directory containing a lv_conf.h file
  -b LVGL_BASE, --lvgl-base LVGL_BASE
                        Root directory for the lvgl source code
  -s START_HEADER_FILE, --start-header-file START_HEADER_FILE
                        Full path to the header file which will be used as the start file for parsing.
  --load-parents LOAD_PARENTS
                        Preload the widget parent information from this file to speedup the parsing. If this file name
                        is not provided the script will not only parse the header files but also the C files to
                        extract the parent classes for widget definitions.
```

How the run it (example for windows using clang as preprocessor) :
```
 python parse_lvgl_files.py cache.json --gcc d:\sandbox\llvm\bin\clang.exe -s ..\lvgl\lvgl.h
```
