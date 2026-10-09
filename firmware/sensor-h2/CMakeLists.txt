# Project ESP-IDF cho node H2 (cảm biến + vòi). Build: idf.py set-target esp32h2 && idf.py build
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(nt532_sensor_h2)
