#!/usr/bin/env python

#init logger
import os,logging, logging.config
logging_file_path=os.path.dirname(__file__)
if logging_file_path !="" :
	logging_file_path+='/logging.conf'
else:
	logging_file_path='logging.conf'
logging.config.fileConfig(logging_file_path)
logger = logging.getLogger(__name__)

#standard python library
import signal,dbus,sys,json,dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
 
#use own version of dbusmonitor
import dbusmonitor



#in case of signal received by the process
def exit_gracefully(signum, stackFrame):
	logger.info(f"{signal.strsignal(signum)}  Received");

	#depending signal
	#saving energy meter values
	if signum==signal.SIGTERM or signum==signal.SIGINT:
		data={'dischargedEnergy_Wh':dischargedEnergy.indexWh,'chargedEnergy_Wh':chargedEnergy.indexWh}

	#or clear them
	elif signum==signal.SIGHUP:
		data={'dischargedEnergy_Wh':0,'chargedEnergy_Wh':0}
		
	with open('energy.json','w') as f:
		json.dump(data,f)
		logger.info(f"Energy meter backup : charged {data['chargedEnergy_Wh']} Wh, discharged {data['dischargedEnergy_Wh']} Wh")
			
	#and request end of dbus glib mainloop
	mainloop.quit()

class DbusEnergyMeter():
	#maximal period for dbus refresh
	REFRESH_PERIOD=60
	def __init__(self,dbusMonitor,serviceName,path,value=0):
		#meter  index in Wh
		self.indexWh=value
		#internal sub index in Joules
		self._indexJ=0
		#noise to be added to the result
		#the aim of this noise is to force meter value to be send to VRM
		#even if the meter value don't vary
		self._noise=0
		self._dbusMonitor=dbusMonitor
		self._serviceName=serviceName
		self._path=path
		self._refreshTimeout=DbusEnergyMeter.REFRESH_PERIOD
		
		#initialise dBus value
		self.dbusRefresh()

	def update(self,power):
		#update Joules index
		self._indexJ+=power

		#and then Wh index
		if self._indexJ >=3600:
			self.indexWh+=self._indexJ // 3600
			self._indexJ %= 3600
			self._refreshTimeout=DbusEnergyMeter.REFRESH_PERIOD

	def dbusRefresh(self):
		#refresh timeout
		self._refreshTimeout+=1

		#if timeout is reached
		if self._refreshTimeout>=DbusEnergyMeter.REFRESH_PERIOD:
			#square noise generation (0.0001 kWh)
			if self._noise==0:
				self._noise=0.0001
			else:
				self._noise=0

			#export result on dBus (meter value in kWh + noise)
			self._dbusMonitor.set_value(self._serviceName,self._path,self.indexWh/1000+self._noise)
			self._refreshTimeout=0
			logger.info(f"Publish Energy meter: {self._path} {self.indexWh} Wh")

#the aim of this function is to calculate average of values (eg Solar charger yield power)
#it shall be run once a second
def energyProcessing():
	#all the code is under try/except to avoid end of the calling timer
	try:
		#get battery power value
		power=dbusMonitor.get_value(battery_service,'/Dc/0/Power')
		
		if power is not None:
			logger.info(f"Current Battery power : {power}")
			
			#if battery power is positive, update charged meter
			if power > 0:
				chargedEnergy.update(power)

			#if battery power is negative, update discharged meter
			if power < 0:
				dischargedEnergy.update(-power)
				
			#refresh dBus values
			chargedEnergy.dbusRefresh()
			dischargedEnergy.dbusRefresh()

		else:
			logger.warning(f"Current Battery power is None")

	except:
		logger.exception("Exception in average processing function")
	
	#return true to not clear Glib periodic timer	
	return True

def serviceRemoved(service, device_instance):
	logger.info(f"Removed service {service} instance {device_instance}")

def serviceAdded(service, device_instance):
	logger.info(f"Added service {service} instance {device_instance}")

if __name__ == "__main__":
	try:
		#log start of service
		logger.critical("Start Battery energy manager")

		#init DBusGMainLoop
		loop=DBusGMainLoop(set_as_default=True)

		#enable dbus object access
		# Why this dummy? To have dbusmonitor happy :-)
		dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
		monitorlist = {'com.victronenergy.battery': {
				'/Dc/0/Power': dummy,
				'/History/DischargedEnergy': dummy,
				'/History/ChargedEnergy': dummy}}

		dbusMonitor = dbusmonitor.DbusMonitor(monitorlist,None,deviceAddedCallback=serviceAdded, deviceRemovedCallback=serviceRemoved)

		#we suppose there is only one CAN battery
		battery_service = list(dbusMonitor.get_service_list('com.victronenergy.battery'))[0]
		logger.info(f"Battery service found : {battery_service}")

		#recover backuped values of energy meters
		try:
			with(open('energy.json','r')) as f:
				data=json.load(f)
				chargedEnergy_Wh=data['chargedEnergy_Wh']
				dischargedEnergy_Wh=data['dischargedEnergy_Wh']
				logger.info(f"Energy meter recovered indexes : charged {chargedEnergy_Wh} Wh, discharged {dischargedEnergy_Wh} Wh")

		except:
			logger.info('Energy meter backup recovery error : Energy meter reset to 0')
			chargedEnergy_Wh=0
			dischargedEnergy_Wh=0

		#init meter
		chargedEnergy=DbusEnergyMeter(dbusMonitor,battery_service,'/History/ChargedEnergy',chargedEnergy_Wh)
		dischargedEnergy=DbusEnergyMeter(dbusMonitor,battery_service,'/History/DischargedEnergy',dischargedEnergy_Wh)

		#energyProcessing function shall be called once par second
		GLib.timeout_add(1000,energyProcessing)

		#launch dbus event loop
		mainloop = GLib.MainLoop()
		
		#save indexes and quit the process on SIGTERM or SIGINT signal
		signal.signal(signal.SIGTERM, exit_gracefully)
		signal.signal(signal.SIGINT,exit_gracefully)
		#clear indexes and quit the process on sighup signal
		signal.signal(signal.SIGHUP,exit_gracefully)

		mainloop.run()
		
		#log end of the loop

		logger.critical("Exit");
		
	except Exception:
		logger.exception("Fatal exception")

