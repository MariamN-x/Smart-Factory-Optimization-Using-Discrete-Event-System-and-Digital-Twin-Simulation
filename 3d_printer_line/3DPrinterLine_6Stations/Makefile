all: clean compile build sim

# Start of user custom code region. Please apply edits only within these regions:  Generic variables

# End of user custom code region. Please don't edit beyond this point.
compile:
# Start of user custom code region. Please apply edits only within these regions:  compile target

# End of user custom code region. Please don't edit beyond this point.

	$(MAKE) -f Makefile.server $@
build:
# Start of user custom code region. Please apply edits only within these regions:  build target

# End of user custom code region. Please don't edit beyond this point.

	$(MAKE) -f Makefile.server $@
	$(MAKE) -f Makefile.client0 $@
	$(MAKE) -f Makefile.client1 $@
	$(MAKE) -f Makefile.client2 $@
	$(MAKE) -f Makefile.client3 $@
	$(MAKE) -f Makefile.client4 $@
	$(MAKE) -f Makefile.client5 $@
	$(MAKE) -f Makefile.client6 $@
sim:
# Start of user custom code region. Please apply edits only within these regions:  sim target

# End of user custom code region. Please don't edit beyond this point.

	vsiSim 3DPrinterLine_6Stations.dt
clean:
# Start of user custom code region. Please apply edits only within these regions:  clean target

# End of user custom code region. Please don't edit beyond this point.

	$(MAKE) -f Makefile.server $@
	$(MAKE) -f Makefile.client0 $@
	$(MAKE) -f Makefile.client1 $@
	$(MAKE) -f Makefile.client2 $@
	$(MAKE) -f Makefile.client3 $@
	$(MAKE) -f Makefile.client4 $@
	$(MAKE) -f Makefile.client5 $@
	$(MAKE) -f Makefile.client6 $@