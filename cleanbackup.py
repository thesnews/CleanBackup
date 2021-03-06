#!/usr/bin/python
"""
	Cleanbackup.py is the State News standardized server backup script
	It started life as a humble Perl script in 2003 and continued to evolve
	from there.
	
	Cleanbackup.py will loop through directories provided in the config file,
	tarball them and drop them into the output folder. If database connection
	information is provided, it'll dump those too.
	
	After running the backup, the script will then remove any backups (of those
	it tracks) that are older than the specified timeout.
	
	See the config file for more information.
	
	Currently only supports MySQLDump for database dumps. SQLite backups are
	done by copying the directory.
	
	2.3 now supports Amazon S3 for remote backup storage. Requires the Boto
	library.
	
	Version: 2.3.1
	Author: mike joseph <josephm5@msu.edu>
	Copyright: 2003-2010 The State News <http://statenews.com>
	License: MIT License
		<http://www.opensource.org/licenses/mit-license.php>
	
	Requires:
		Python 2.5+
		PyYAML <http://pyyaml.org/wiki/PyYAML>
		Boto <http://code.google.com/p/boto>
			Boto is only required for Amazon S3 remote storage integration
"""

VERSION = '2.3.1'
LOGFILE = '/var/log/cleanbackup.log'

import sys
import os
import yaml
import tarfile
import gzip
import commands
import logging
import logging.handlers

from optparse import OptionParser
from time import time, localtime, strftime

# make sure file stats always return floats
os.stat_float_times(True)

# MAIN
def main() :
	configData = yaml.load(loadConfig(options.config))

	remoteStorage = configData.get('remoteStore')
	s3Bucket = False
	
	if remoteStorage and remoteStorage.get('enabled') :
		from boto.s3.connection import S3Connection
		from boto.s3.key import Key
		
		s3Conn = S3Connection(remoteStorage.get('awsKey'),
								remoteStorage.get('awsSecret'))
		s3Bucket = s3Conn.get_bucket(remoteStorage.get('awsBucket'))
	else :
		remoteStorage = False

	outPath = configData.get('localStore')

	timeString = strftime('%Y%m%d')
	
	if not os.access(outPath, os.W_OK) :
		logOutput("Could not open outdir: %s" % outPath, level='warning')
		exit(3)
	
	# process new file tarballs
	if configData.get('localFiles') :
		logOutput('Starting file backup', level='info')
		for p in configData.get('localFiles') :
			
			tarPath = os.path.join(outPath, '%s_%s.tgz' %
									(os.path.basename(p), timeString))
	
			tar = tarfile.open( tarPath, 'w:gz')
			logOutput("Tarballing %s" % p, level='info')
			
			# arcname keeps tarball from rebuilding entire tree leading 
			# up to dir
			tar.add(p, os.path.basename(p))
			info = tar.gettarinfo(tarPath)
	
			logOutput("Tarballed %s @ %i bytes" % (info.name, info.size),
						level='info')
	
			tar.close()
			
			if remoteStorage and s3Bucket :
				s3Key = Key(s3Bucket)
				s3Key.key = '%s_%s.tgz' % (os.path.basename(p), timeString)

				logOutput('Sending to Amazon S3', level='info')
				s3Key.set_contents_from_filename(tarPath)
				s3Key.set_metadata( 'create-time', time() )

				s3Key.close()
				s3Key = False
	else :
		logOutput("No files to tarball", level='info')

	if configData.get('localDatabases') :
		logOutput('Starting database backup', level='info')
		
		for db in configData.get('localDatabases') :
			u = db.get('username')
			p = db.get('password')
			
			myBin = os.path.join(configData.get('mysqlbin'), 'mysqldump')
			
			for d in db.get('databases') :

				dumpPath = os.path.join(outPath, 
										'%s_%s.sql' % (d, timeString))
				cmd = '%s -p -c -u %s --password=%s %s > %s' % (myBin, u, p, d,
						dumpPath)
				if commands.getstatusoutput(cmd)[0] == 0 :
					# file dumped, gzip it and remove original dump
					handle = open(dumpPath)
					gzPath = os.path.join(outPath, '%s_%s.sql.gz' % 
						(d, timeString))
					gzfile = gzip.open(gzPath, 'wb')
					gzfile.writelines(handle)
					gzfile.close()
					handle.close()
					
					os.remove(dumpPath)
					
					logOutput('Dumped and zipped %s' % d, level='info')

					if remoteStorage and s3Bucket :
						s3Key = Key(s3Bucket)
						s3Key.key = '%s_%s.sql' % (d, timeString)
		
						logOutput('Sending to Amazon S3', level='info')
						s3Key.set_contents_from_filename(gzPath)
						s3Key.set_metadata( 'create-time', time() )
		
						s3Key.close()
						s3Key = False

				else :
					logOutput('Unable to dump %s' % d, level='warning')
	else :
		logOutput('No databases to export', level='info')
	
	logOutput('Backup operations complete', level='info')
	
	if configData.get('autoclean') :
		logOutput('Starting autoclean', level='info')
		logOutput('Removing anything older than %i day(s)' %
					configData.get('autoclean'))
		# autoclean value X number of seconds in a day
		delta = time() - (configData.get('autoclean') * 86400)
		for readPath, dirs, files in os.walk(outPath) :
			for f in files :
				if os.stat(os.path.join(readPath, f))[8] < delta :
					logOutput('Removing %s' % f, level='info')
					os.remove(os.path.join(readPath, f))
					
		if remoteStorage and s3Bucket:
			logOutput('Removing old files from AmazonS3', level='info')
			for k in s3Bucket.get_all_keys() :
				if(k.get_metadata('create-time') and
					k.get_metadata('create-time') < delta) :
					logOutput('Removing %s' % k.key, level='info')
					k.delete()
	
	logOutput('Done', level='info')
	
# end MAIN

# LOADCONFIG
def loadConfig( configFile ) :
	# configs live in same directory as executable
	filePath = os.path.join(os.path.abspath(os.path.dirname(__file__)),
							configFile + '.config.yaml')
							
	if not os.access(filePath, os.R_OK) :
		logOutput('%s not found' % filePath, level='warning')
		exit(2)

	handle = open(filePath)
	data = handle.read()
	handle.close
	
	return data
# end LOADCONFIG

# LOGOUTPUT
def logOutput( string, **kwargs ) :
	level = 'debug'
	if kwargs.get('level') :
		level = kwargs.get('level')
		
	if level == 'info' :
		logger.info( string )
	elif level == 'warning' :
		logger.warning( string )
	elif level == 'error' :
		logger.error( string )
	elif level == 'critical' :
		logger.critical( string )
	else :
		logger.debug( string )
	
	if options.verbose or level == 'warning' :
		print string
		
# end LOGOUTPUT

logger = logging.getLogger('CleanBackupLogger')

try :
	logHandler = logging.handlers.RotatingFileHandler( LOGFILE,
														maxBytes=1024000,
														backupCount=6)
	logHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - "+
												"%(message)s"))
	logger.addHandler(logHandler)
except :
	print "Could not load logger."

parser = OptionParser(version='%prog ' + VERSION)
parser.add_option('--config', dest='config', type='string',
					help='Config file to use')
parser.add_option('-v', '--verbose', dest='verbose', action='store_true',
					help='Verbose output')
parser.add_option('--debug', dest='debug', action='store_true',
					help='Debug mode')
					
(options, args) = parser.parse_args()

if options.debug :
	logger.setLevel(logging.DEBUG)
else :
	logger.setLevel(logging.INFO)

logOutput('Cleanbackup started')
	
if not options.config :
	logOutput('Config file not set', level='error')
	parser.print_help()
	exit(1)
	
main()