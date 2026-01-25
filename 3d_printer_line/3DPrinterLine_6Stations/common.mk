SOCKET_DOMAIN = AF_UNIX
CC = gcc
CXX = g++
OPT=-O3

CDEFINES += \

CFLAGS += \
	$(OPT) -fPIC -Wall $(CDEFINES)

INCL = \
	-I$(XL_VIP)\

XL_VIP_LIBS = \
    $(XL_VIP_OBJ)/xl_vip_open_kit.so \
    $(XL_VIP_OBJ)/xl_vip_tlm_xactors.so \
    $(XL_VIP_OBJ)/xl_vip_open_kit_stubs.so \
    $(XL_VIP_OBJ)/xl_vip_open_kit_extras.so \

LDFLAGS += \
	-ldl -Wl,-rpath,$(VSI_LIB_OBJ)

UVMC_LIB_SO = \
	$(UVMC_LIB_OBJ)/uvmc_tlm_fabric.so \

COBJS=$(CSRCS:%.c=%.o)
OBJS=$(SRCS:%.cxx=%.o)
TOBJS=$(TSRCS:%.cxx=%.o)

%.o: %.c
	$(CC) $(CFLAGS) $(INCL) -c $< -o $@

%.o: %.cxx
	$(CXX) $(CFLAGS) $(INCL) -c $< -o $@


