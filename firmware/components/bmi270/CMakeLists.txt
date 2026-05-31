idf_component_register(
    SRCS "bmi2.c" "bmi270.c" "bmi270_idf.c"
    INCLUDE_DIRS "."
    REQUIRES driver
)

target_compile_options(${COMPONENT_LIB} PRIVATE
    -Wno-unused-variable
    -Wno-unused-function
    -Wno-sign-compare
    -Wno-ignored-qualifiers
)
