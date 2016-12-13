"""
    Title: spiders.py
    Author: Benjamin Yvernault
    contact: b.yvernault@ucl.ac.uk
    Purpose:
        Spider base class and class for Scan and Session spider
        Spider name must be: Spider_[name]_v[version].py
        Utils for spiders
"""

import collections
import csv
import getpass
from dax import XnatUtils
from datetime import datetime
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
import os
import re
from scipy.misc import imresize
from shutil import copyfile, copytree, copy, rmtree
from stat import S_IXUSR, ST_MODE
from string import Template
import subprocess as sb
import sys
import time

FSLSWAP_VAL = {0: 'x',
               1: 'y',
               2: 'z'}

__author__ = 'Benjamin Yvernault'
__email__ = 'b.yvernault@ucl.ac.uk'
__purpose__ = "Spider base class, Scan and Session spider class, and Utils \
for spiders."
__version__ = '1.0.0'
__modifications__ = '26 August 2015 - Original write'

UNICODE_SPIDER = """
Spider information:
  -- General --
    path:    {path}
    jobdir:  {jobdir}
    suffix:  {suffix}
  -- XNAT --
    host:    {host}
    user:    {user}
    project: {project}
    subject: {subject}
    session: {session}
{extra}
"""


class Spider(object):
    """ Base class for spider """
    def __init__(self, spider_path, jobdir,
                 xnat_project, xnat_subject, xnat_session,
                 xnat_host=None, xnat_user=None, xnat_pass=None,
                 suffix="", subdir=True, skip_finish=False):
        """
        Entry point for the Base class for spider

        :param spider_path: spider file path
        :param jobdir: directory for temporary files
        :param xnat_project: project ID on XNAT
        :param xnat_subject: subject label on XNAT
        :param xnat_session: experiment label on XNAT
        :param xnat_host: host for XNAT if not set in environment variables
        :param xnat_user: user for XNAT if not set in environment variables
        :param xnat_pass: password for XNAT if not set in environment variables
        :param suffix: suffix to the assessor creation
        :param subdir: create a subdir Temp in the jobdir if the directory
                       isn't empty.
        """
        # Spider path:
        self.spider_path = spider_path
        # directory for temporary files + create it
        self.jobdir = XnatUtils.makedir(os.path.abspath(jobdir), subdir=subdir)
        # to copy results at the end
        self.spider_handler = None
        # Xnat info:
        self.xnat_project = xnat_project
        self.xnat_subject = xnat_subject
        self.xnat_session = xnat_session
        # Xnat connection settings:
        self.host = get_default_value("host", "XNAT_HOST", xnat_host)
        self.user = get_default_value("user", "XNAT_USER", xnat_user)
        self.pwd = get_pwd(xnat_host, xnat_user, xnat_pass)
        # Suffix
        if not suffix:
            self.suffix = ""
        else:
            # Set the suffix_proc remove any special characters, replace by '_'
            self.suffix = re.sub('[^a-zA-Z0-9]', '_', suffix)
            # Replace multiple underscores by one
            self.suffix = re.sub('_+', '_', self.suffix)
            # Remove underscore if at the end of suffix
            if self.suffix[-1] == '_':
                self.suffix = self.suffix[:-1]
            # Add an underscore at the beginning if not present
            if self.suffix[0] != '_':
                self.suffix = '_'+self.suffix
        # print time writer:
        self.time_writer = TimedWriter(use_date=True)
        # Export the variable:
        os.environ['XNAT_HOST'] = self.host
        os.environ['XNAT_USER'] = self.user
        os.environ['XNAT_PASS'] = self.pwd
        # run the finish or not
        self.skip_finish = skip_finish
        # Inputs:
        self.inputs = None
        # data:
        self.data = list()
        # cmd arguments:
        self.cmd_args = list()

    def __unicode__(self):
        """ Unicode for spiders."""
        extra = '  -- Extra --\n'
        # if inputs
        if self.inputs:
            unicode_inputs = '    Inputs:\n'
            for in_dict in self.inputs:
                v = ("type: %s - label: %s - res: %s"
                     % (in_dict.get('type'), in_dict.get('label'),
                        in_dict.get('resource')))
                unicode_inputs = '%s        %s\n' % (unicode_inputs, v)
            extra += unicode_inputs
        # if data downloaded
        if self.data:
            unicode_data = '    Data:\n'
            for in_dict in self.data:
                v = ("label: %s - files: %s "
                     % (in_dict.get('label'), in_dict.get('files')))
                unicode_data = '%s        %s\n' % (unicode_data, v)
            extra += unicode_data
        return UNICODE_SPIDER.format(
                path=self.spider_path,
                jobdir=self.jobdir,
                suffix=self.suffix,
                host=self.host,
                user=self.user,
                project=self.xnat_project,
                subject=self.xnat_subject,
                session=self.xnat_session,
                extra=extra,
                )

    def __str__(self):
        return unicode(self).encode('utf-8')

    @staticmethod
    def get_data_dict(otype, label, resource, directory, scan=None):
        """Create a data_dict for self.inputs from user need."""
        return {'type': otype,
                'label': label,
                'resource': resource,
                'dir': directory,
                'scan': scan}

    def select_obj(self, intf, obj_label, resource):
        """
        Select scan or assessor resource

        :param obj_label: xnat object label (scan ID or assessor label)
        :param resource: folder name under the xnat object
        return pyxnat object

        """
        tmp_dict = collections.OrderedDict([('project', self.xnat_project),
                                            ('subject', self.xnat_subject),
                                            ('experiment', self.xnat_session)])
        # Check the scan
        tmp_dict_scan = tmp_dict.copy()
        tmp_dict_scan['scan'] = obj_label
        tmp_dict_scan['resource'] = resource
        xnat_obj = intf.select(self.select_str(tmp_dict_scan))
        if xnat_obj:
            return xnat_obj
        else:
            # Otherwise check the assessor
            tmp_dict_assessor = tmp_dict.copy()
            tmp_dict_assessor['assessor'] = obj_label
            tmp_dict_assessor['out/resource'] = resource
            xnat_obj = intf.select(self.select_str(tmp_dict_assessor))
            if xnat_obj:
                return xnat_obj
            else:
                # Error: not on XNAT
                err = "No XNAT Object found with the following values: "
                err += str(tmp_dict)
                err += "\n scan or assessor: %s / resource: %s " % (obj_label,
                                                                    resource)
                raise ValueError(err)

    def download_inputs(self):
        """
        Download inputs data from XNAT define in self.inputs.

        self.inputs = list of data dictionary with keys define below
        keys:
            'type': 'scan' or 'assessor' or 'subject' or 'project' or 'session'
            'label': label on XNAT (not needed for session/subject/project)
            'resource': name of resource to download
            'dir': directory to download files into (optional)
          - for assessor only if not giving the label but just proctype
            'scan': id of the scan for the assessor (if None, sessionAssessor)

        self.data = list of dictionary with keys define below:
            'label': label on XNAT
            'files': list of files downloaded

        set self.data, a python list of the data downloaded.
        """
        if not self.inputs:
            raise Exception('ERROR using download_inputs(): \
self.inputs not define in your spider.')
        if not isinstance(self.inputs, list):
            raise Exception('ERROR: self.inputs is not a list: %s'
                            % self.inputs)
        # Inputs folder: jobdir/inputs
        input_dir = os.path.join(self.jobdir, 'inputs')
        xnat = XnatUtils.get_interface(host=self.host, user=self.user,
                                       pwd=self.pwd)
        for data_dict in self.inputs:
            if not isinstance(data_dict, dict):
                raise Exception('ERROR: data in self.inputs is not a dict: %s'
                                % data_dict)
            if 'dir' in data_dict.keys():
                data_folder = os.path.join(input_dir, data_dict['dir'])
            else:
                data_folder = os.path.join(input_dir, data_dict['label'])
            self.time_writer(' downloading %s for %s into %s'
                             % (data_dict['resource'], data_dict['label'],
                                data_folder))
            if not os.path.isdir(data_folder):
                os.makedirs(data_folder)
            xnat_dict = self.get_xnat_dict(data_dict)
            res_str = self.select_str(xnat_dict)
            resource_obj = xnat.select(res_str)
            resource_obj.get(data_folder, extract=True)
            resource_dir = os.path.join(data_folder, resource_obj.label())
            # Move files to the data_folder
            for src in os.listdir(resource_dir):
                src_path = os.path.join(resource_dir, src)
                if os.path.isdir(src_path):
                    copytree(src_path, data_folder)
                elif os.path.isfile(src_path):
                    copy(src_path, data_folder)
            rmtree(resource_dir)
            # Get all the files downloaded
            list_files = list()
            for root, _, filenames in os.walk(data_folder):
                list_files.extend([os.path.join(root, filename)
                                   for filename in filenames])
            item = [d for d in self.data if d['label'] == data_dict['label']]
            if item:
                item[0]['files'] = list(set(list_files+item[0]['files']))
            else:
                self.data.append({
                    'label': data_dict['label'],
                    'files': list_files
                })
        xnat.disconnect()

    def get_xnat_dict(self, data_dict):
        """Return a OrderedDict dictionary with XNAT information.

        keys:
            project
            subject
            experiment
            scan
            resource
            assessor
            out/resource  (for assessor)
        """
        xdict = collections.OrderedDict([('project', self.xnat_project)])
        itype = data_dict.get('type', None)
        if not itype:
            print "Warning: 'type' not specified in inputs %s" % data_dict
            return None
        label = data_dict.get('label', None)
        if itype == 'subject':
            xdict['subject'] = self.xnat_subject
        elif itype == 'session':
            xdict['subject'] = self.xnat_subject
            xdict['experiment'] = self.xnat_session
        elif itype == 'scan':
            xdict['subject'] = self.xnat_subject
            xdict['experiment'] = self.xnat_session
            if not label:
                print "Warning: 'label' not specified in inputs %s" % data_dict
                return None
            else:
                xdict['scan'] = data_dict.get('label', None)
        elif itype == 'assessor':
            xdict['subject'] = self.xnat_subject
            xdict['experiment'] = self.xnat_session
            if not label:
                print "Warning: 'label' not specified in inputs %s" % data_dict
                return None
            else:
                scan_id = data_dict.get('scan', None)
                xdict['assessor'] = self.get_assessor_label(label, scan_id)

        if data_dict == 'assessor':
            xdict['out/resource'] = data_dict.get('resource', None)
        else:
            xdict['resource'] = data_dict.get('resource', None)
        return xdict

    def get_assessor_label(self, label, scan):
        tmp_list = [self.xnat_project,
                    self.xnat_subject,
                    self.xnat_session]
        if '-x-' not in label:
            if not scan:
                tmp_list.append(label)
            else:
                tmp_list.append(scan)
                tmp_list.append(label)
            return '-x-'.join(tmp_list)
        else:
            return label

    def download(self, obj_label, resource, folder):
        """
        Return a python list of the files downloaded for the scan's resource
            example:
              download(scan_id, "DICOM", "/Users/test")
             or
              download(assessor_label, "DATA", "/Users/test")

        :param obj_label: xnat object label (scan ID or assessor label)
        :param resource: folder name under the xnat object
        :param folder: download directory
        :return: python list of files downloaded
        """
        # Open connection to XNAT
        xnat = XnatUtils.get_interface(host=self.host, user=self.user,
                                       pwd=self.pwd)
        resource_obj = self.select_obj(intf=xnat,
                                       obj_label=obj_label,
                                       resource=resource)
        list_files = XnatUtils.download_files_from_obj(
                    directory=folder, resource_obj=resource_obj)
        # close connection
        xnat.disconnect()
        return list_files

    def define_spider_process_handler(self):
        """
        Define the SpiderProcessHandler so the file(s) and PDF are checked for
         existence and uploaded to the upload_dir accordingly.
        Implemented in derived classes.

        :raises: NotImplementedError() if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def has_spider_handler(self):
        """
        Check to see that the SpiderProcessHandler is defined. If it is not,
         call define_spider_process_handler

        :return: None

        """
        if not self.spider_handler:
            self.define_spider_process_handler()

    def upload(self, fpath, resource):
        """
        Upload files to the queue on the cluster to be upload to XNAT by DAX
        E.g: spider.upload("/Users/DATA/", "DATA")
         spider.upload("/Users/stats_dir/statistical_measures.txt", "STATS")

        :param fpath: path to the folder/file to be uploaded
        :param resource: folder name to upload to on the assessor
        :raises: ValueError if the file to upload does not exist
        :return: None

        """
        self.has_spider_handler()
        if os.path.isfile(fpath):
            if resource == 'PDF':
                self.spider_handler.add_pdf(fpath)
            else:
                self.spider_handler.add_file(fpath, resource)
        elif os.path.isdir(fpath):
            self.spider_handler.add_folder(fpath, resource)
        else:
            err = "upload(): file path does not exist: %s" % (fpath)
            raise ValueError(err)

    def upload_dict(self, files_dict):
        """
        upload files to the queue on the cluster to be upload to XNAT by DAX
         following the files python dictionary: {resource_name : fpath}
        E.g: fdict = {"DATA" : "/Users/DATA/", "PDF": "/Users/PDF/report.pdf"}
         spider.upload_dict(fdict)

        :param files_dict: python dictionary containing the pair resource/fpath
        :raises: ValueError if the filepath is not a string or a list
        :return: None

        """
        self.has_spider_handler()
        for resource, fpath in files_dict.items():
            if isinstance(fpath, str):
                self.upload(fpath, resource)
            elif isinstance(fpath, list):
                for ffpath in fpath:
                    self.upload(ffpath, resource)
            else:
                err = "upload_dict(): variable not recognize in dictionary \
for resource %s : %s"
                raise ValueError(err % (resource, type(fpath)))

    def end(self):
        """
        Finish the script by sending the end of script flag and cleaning folder

        :param jobdir: directory for the spider
        :return: None

        """
        self.has_spider_handler()
        self.spider_handler.done()
        self.spider_handler.clean(self.jobdir)
        self.print_end()

    def check_executable(self, executable, name):
        """Method to check the executable.

        :param executable: executable path
        :param name: name of Executable
        :return: Complete path to the executable
        """
        if executable == name:
            # Check the output of which:
            pwhich = sb.Popen(['which', executable],
                              stdout=sb.PIPE,
                              stderr=sb.PIPE)
            results, _ = pwhich.communicate()
            if not results or results.startswith('/usr/bin/which: no') or \
               results == '':
                raise Exception("Executable '%s' not found on your computer."
                                % (name))
        else:
            executable = os.path.abspath(executable)
            if executable.endswith(name):
                pass
            elif os.path.isdir(executable):
                executable = os.path.join(executable, name)
            if not os.path.exists(executable):
                raise Exception("Executable '%s' not found" % (executable))

        pversion = sb.Popen([executable, '--version'],
                            stdout=sb.PIPE,
                            stderr=sb.PIPE)
        nve_version, _ = pversion.communicate()
        self.time_writer('%s version: %s' %
                         (name, nve_version.strip()))
        return executable

    def pre_run(self):
        """
        Pre-Run method to download and organise inputs for the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def run(self):
        """
        Runs the "core" or "image processing process" of the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def finish(self):
        """
        Method to copy the results in the Spider Results folder dax.RESULTS_DIR
        Implemented in derived class objects.

        :raises: NotImplementedError if not overriden by user
        :return: None
        """
        raise NotImplementedError()

    def print_init(self, argument_parse, author, email):
        """
        Print a message to display information on the init parameters, author,
        email, and arguments using time writer

        :param argument_parse: argument parser
        :param author: author of the spider
        :param email: email of the author
        :return: None
        """
        self.print_info(author, email)
        self.time_writer('-------- Spider starts --------')
        self.time_writer('Date and Time at the beginning of the Spider: %s'
                         % str(datetime.now()))
        self.time_writer('INFO: Arguments')
        self.print_args(argument_parse)

    def print_msg(self, message):
        """
        Print message using time writer

        :param message: string displayed for the user
        :return: None
        """
        self.time_writer(message)

    def print_err(self, err_message):
        """
        Print error message using time writer

        :param err_message: error message displayed for the user
        :return: None
        """
        self.time_writer.print_stderr_message(err_message)

    def print_info(self, author, email):
        """
        Print information on the spider using time writer

        :param author: author of the spider
        :param email: email of the author
        :return: None
        """
        self.print_msg("Running spider : %s" % (self.spider_path))
        self.print_msg("Spider Author: %s" % (author))
        self.print_msg("Author Email:  %s" % (email))

    def print_args(self, argument_parse):
        """
        print arguments given to the Spider

        :param argument_parse: argument parser
        :return: None
        """
        self.time_writer("-- Arguments given to the spider --")
        for info, value in sorted(vars(argument_parse).items()):
            self.time_writer("%s : %s" % (info, value))
        self.time_writer("-----------------------------------")

    def print_end(self):
        """
        Last print statement to give the time and date at the end of the spider

        :return: None
        """
        self.time_writer('Time at the end of the Spider: %s'
                         % str(datetime.now()))

    def plot_images_page(self, pdf_path, page_index, nii_images, title,
                         image_labels, slices=None, cmap='gray',
                         vmins=None, vmaxs=None, volume_ind=None):
        """Plot list of images (3D-4D) on a figure (PDF page).

        See function at the end of the file.
        """
        return plot_images(
            pdf_path=pdf_path, page_index=page_index, nii_images=nii_images,
            title=title, image_labels=image_labels, slices=slices, cmap=cmap,
            vmins=vmins, vmaxs=vmaxs, volume_ind=volume_ind,
            time_writer=self.time_writer)

    def plot_stats_page(self, pdf_path, page_index, stats_dict, title,
                        tables_number=3, columns_header=['Header', 'Value'],
                        limit_size_text_column1=30,
                        limit_size_text_column2=10):
        """Generate pdf report of stats information from a csv/txt.

        See function at the end of the file.
        """
        return plot_stats(
            pdf_path=pdf_path, page_index=page_index, stats_dict=stats_dict,
            title=title, tables_number=tables_number,
            columns_header=columns_header,
            limit_size_text_column1=limit_size_text_column1,
            limit_size_text_column2=limit_size_text_column2,
            time_writer=self.time_writer)

    def merge_pdf(self, pdf_pages, pdf_final):
        """Concatenate all pdf pages in the list into a final pdf.

        See function at the end of the file.
        """
        return merge_pdfs(pdf_pages, pdf_final, self.time_writer)

    def run_cmd_args(self, matlab=False, matlab_template=None,
                     suffix=""):
        """
        Run a command line via os.system() with arguments set in self.cmd_args

        cmd_args is a dictionary:
            exe: path to executable to run
            args: list of arguments to pass (list of dicitonaries):
                position: int to specify the order of parameters
                opt: options to set (-i for example) or value in template
                value: value of the argument
        :param matlab: matlab script
        :param matlab_template: template string for matlab script
        :param suffix: suffix for matlab script name
        :return: None
        """
        if not self.cmd_args:
            raise Exception("self.cmd_args not defined.")
        if 'exe' not in self.cmd_args.keys():
            raise Exception("self.cmd_args doesn't have a key 'exe'.")
        if 'args' not in self.cmd_args.keys():
            raise Exception("self.cmd_args doesn't have a key 'args'.")
        if matlab:
            if not matlab_template:
                raise Exception("string template for matlab not set.")
            mat_args = dict()
            for arg in self.cmd_args['args']:
                mat_args[arg['opt']] == arg['value']
            mat_lines = matlab_template.format(mat_args)
            name = 'run_matlab_cmd.m'
            mat_name = 'run_matlab_cmd_%s.m' % suffix if suffix else name
            matlab_script = os.path.join(self.jobsdir, mat_name)
            with open(matlab_script, "w") as f:
                f.writelines(mat_lines)
            self.time_writer("Running matlab script: %s" % matlab_script)
            XnatUtils.run_matlab(matlab_script, verbose=True)
        else:
            cmd = self.cmd_args['exe']
            for arg in sorted(self.cmd_args['args'],
                              key=lambda k: int(k['position'])):
                cmd += "%s %s " % (arg['opt'], arg['value'])
            self.time_writer("Running command: %s" % cmd)
            self.run_system_cmd(cmd)

    @staticmethod
    def run_system_cmd(cmd):
        """
        Run system command line via os.system()

        :param cmd: command to run
        :return: None
        """
        os.system(cmd)

    @staticmethod
    def select_str(xnat_dict):
        """
        Return string for pyxnat to select object from python dict

        :param tmp_dict: python dictionary with xnat information
            keys = ["project", "subject", "experiement", "scan", "resource"]
              or
            keys = ["project", "subject", "experiement", "assessor",
                    "out/resource"]
        :return string: string path to select pyxnat object
        """
        select_str = ''
        for key, value in xnat_dict.items():
            if value:
                select_str += '''/{key}/{label}'''.format(key=key, label=value)
        return select_str


class ScanSpider(Spider):
    """ Derived class for scan-spider """
    def __init__(self, spider_path, jobdir,
                 xnat_project, xnat_subject, xnat_session, xnat_scan,
                 xnat_host=None, xnat_user=None, xnat_pass=None,
                 suffix="", subdir=True):
        """
        Entry point for Derived class for Spider on Scan level

        :param super --> see base class
        :param xnat_scan: scan ID on XNAT (if running on a specific scan)
        """
        super(ScanSpider, self).__init__(
            spider_path, jobdir,
            xnat_project, xnat_subject, xnat_session,
            xnat_host, xnat_user, xnat_pass,
            suffix, subdir)
        self.xnat_scan = xnat_scan

    def __unicode__(self):
        """ Unicode for spiders."""
        unicode_base = super(ScanSpider, self).__unicode__()
        return unicode_base + '\n    scan:    %s' % self.xnat_scan

    def define_spider_process_handler(self):
        """
        Define the SpiderProcessHandler for the end of scan spider
         using the init attributes about XNAT

        :return: None
        """
        # Create the SpiderProcessHandler if first time upload
        self.spider_handler = XnatUtils.SpiderProcessHandler(
                self.spider_path,
                self.suffix,
                self.xnat_project,
                self.xnat_subject,
                self.xnat_session,
                self.xnat_scan,
                time_writer=self.time_writer)

    def pre_run(self):
        """
        Pre-Run method to download and organise inputs for the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def run(self):
        """
        Runs the "core" or "image processing process" of the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def finish(self):
        """
        Method to copy the results in the Spider Results folder dax.RESULTS_DIR
        Implemented in derived class objects.

        :raises: NotImplementedError if not overriden by user
        :return: None
        """
        raise NotImplementedError()


class SessionSpider(Spider):
    """ Derived class for session-spider """
    def __init__(self, spider_path, jobdir,
                 xnat_project, xnat_subject, xnat_session,
                 xnat_host=None, xnat_user=None, xnat_pass=None,
                 suffix="", subdir=True):
        """
        Entry point for Derived class for Spider on Session level

        :param super --> see base class
        """
        super(SessionSpider, self).__init__(
            spider_path, jobdir,
            xnat_project, xnat_subject, xnat_session,
            xnat_host, xnat_user, xnat_pass, suffix, subdir)

    def define_spider_process_handler(self):
        """
        Define the SpiderProcessHandler for the end of session spider
         using the init attributes about XNAT

        :return: None
        """
        # Create the SpiderProcessHandler if first time upload
        self.spider_handler = XnatUtils.SpiderProcessHandler(
                self.spider_path,
                self.suffix,
                self.xnat_project,
                self.xnat_subject,
                self.xnat_session,
                time_writer=self.time_writer)

    def pre_run(self):
        """
        Pre-Run method to download and organise inputs for the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def run(self):
        """
        Runs the "core" or "image processing process" of the pipeline
        Implemented in derived class objects.

        :raises: NotImplementedError if not overridden.
        :return: None
        """
        raise NotImplementedError()

    def finish(self):
        """
        Method to copy the results in the Spider Results folder dax.RESULTS_DIR
        Implemented in derived class objects.

        :raises: NotImplementedError if not overriden by user
        :return: None
        """
        raise NotImplementedError()


class AutoSpider(Spider):
    def __init__(self, name, params, outputs, template, datatype='session'):
        self.name = name
        self.params = params
        self.outputs = outputs
        self.template = template
        self.datatype = datatype
        self.copy_list = []

        # Make the parser
        parser = self.get_argparser()

        # Now parse commandline arguments
        args = parser.parse_args()
        print(args)

        # Initialize spider with the args
        super(AutoSpider, self).__init__(
            name,
            args.temp_dir, args.proj_label, args.subj_label, args.sess_label,
            xnat_host=args.host, xnat_user=args.user, xnat_pass=None,
            suffix=args.suffix, skip_finish=args.skipfinish)

        if datatype == 'scan':
            self.xnat_scan = args.scan_label

        # Make a list of parameters that need to be copied to our input
        # directory
        for p in params:
            if p[1] == 'FILE' or p[1] == 'DIR':
                self.copy_list.append(p[0])

        self.src_inputs = vars(args)
        # reset in case it changed in parent init
        self.src_inputs['temp_dir'] = self.jobdir
        self.input_dir = os.path.join(self.jobdir, 'INPUT')
        self.script_dir = os.path.join(self.jobdir, 'SCRIPT')
        self.run_inputs = {}

    def get_argparser(self):
        if self.datatype == 'scan':
            parser = get_scan_argparser(self.name, 'Run '+self.name)
        else:
            parser = get_session_argparser(self.name, 'Run '+self.name)

        # Add input params to arguments
        for p in self.params:
            parser.add_argument('--'+p[0], dest=p[0], help=p[2], required=True)

        return parser

    def define_spider_process_handler(self):
        """
        Define the SpiderProcessHandler for the end of spider
         using the init attributes about XNAT
        :return: None
        """
        # Create the SpiderProcessHandler if first time upload
        if self.datatype == 'scan':
            self.spider_handler = XnatUtils.SpiderProcessHandler(
                self.spider_path,
                self.suffix,
                self.xnat_project,
                self.xnat_subject,
                self.xnat_session,
                self.xnat_scan,
                time_writer=self.time_writer
            )

        else:
            self.spider_handler = XnatUtils.SpiderProcessHandler(
                self.spider_path,
                self.suffix,
                self.xnat_project,
                self.xnat_subject,
                self.xnat_session,
                time_writer=self.time_writer
            )

    def copy_inputs(self):
        self.run_inputs = self.src_inputs

        os.mkdir(self.input_dir)

        for _input in self.copy_list:
            # Split the list and handle each copy each individual file/dir
            src_list = self.src_inputs[_input].split(',')
            dst_list = []
            for i, src in enumerate(src_list):
                input_name = _input+'_'+str(i)
                dst = self.copy_input(src, input_name)
                if not dst:
                    print('ERROR:copying inputs')
                    return None
                else:
                    dst_list.append(dst)

            # Build new comma-separated list with local paths
            self.run_inputs[_input] = ','.join(dst_list)

        return self.run_inputs

    def go(self):

        self.pre_run()

        self.run()

        if not self.skip_finish:
            self.finish()

    def pre_run(self):
        '''Pre-Run method to download and organise inputs for the pipeline
        Implemented in derived class objects.'''
        print('DEBUG:pre_run()')
        self.copy_inputs()

    def run(self):
        print('DEBUG:run()')
        os.mkdir(self.script_dir)

        if self.template.startswith('#PYTHON'):
            self.run_python(self.template, 'script.py')
        elif self.template.startswith('%MATLAB'):
            self.run_matlab(self.template, 'script.m')
        else:
            self.run_shell(self.template, 'script.sh')

    def finish(self):
        print('DEBUG:finish()')

        self.has_spider_handler()

        # Add each output
        if self.outputs:
            for _output in self.outputs:
                _path = os.path.join(self.jobdir, _output[0])
                _type = _output[1]
                _res = _output[2]

                if _type == 'FILE':
                    if _res == 'PDF':
                        self.spider_handler.add_pdf(_path)
                    else:
                        self.spider_handler.add_file(_path, _res)
                elif _type == 'DIR':
                    self.spider_handler.add_folder(_path, _res)
                else:
                    print('ERROR:unknown type:'+_type)
        else:
            for _output in os.listdir(self.jobdir):
                _path = os.path.join(self.jobdir, _output)
                _res = os.path.basename(_output)
                self.spider_handler.add_folder(_path, _res)

        self.end()

    def run_matlab(self, mat_template, filename):
        filepath = os.path.join(self.script_dir, filename)
        template = Template(mat_template)

        # Write the script
        with open(filepath, 'w') as f:
            f.write(template.substitute(self.run_inputs))

        # Run the script
        XnatUtils.run_matlab(filepath, verbose=True)

    def run_shell(self, sh_template, filename):
        filepath = os.path.join(self.script_dir, filename)
        template = Template(sh_template)

        # Write the script
        with open(filepath, 'w') as f:
            f.write(template.substitute(self.run_inputs))

        # Run it
        os.chmod(filepath, os.stat(filepath)[ST_MODE] | S_IXUSR)
        os.system(filepath)

    def run_python(self, py_template, filename):
        filepath = os.path.join(self.script_dir, filename)
        template = Template(py_template)

        # Write the script
        with open(filepath, 'w') as f:
            f.write(template.substitute(self.run_inputs))

        # Run it
        os.chmod(filepath, os.stat(filepath)[ST_MODE] | S_IXUSR)
        os.system('python '+filepath)

    def copy_input(self, src, input_name):

        if self.is_xnat_uri(src):
            print('DEBUG:copying xnat input:'+src)
            src = self.parse_xnat_uri(src)
            dst = self.copy_xnat_input(src, input_name)
        else:
            print('DEBUG:copying local input:'+src)
            dst = self.copy_local_input(src, input_name)

        return dst

    def copy_xnat_input(self, src, input_name):
        dst_dir = os.path.join(self.input_dir, input_name)
        os.makedirs(dst_dir)

        if '/files/' in src:
            # Handle file
            _res, _file = src.split('/files/')
            dst = os.path.join(dst_dir, _file)

            print('DEBUG:downloading from XNAT:'+src+' to '+dst)
            result = self.download_xnat_file(src, dst)
            return result

        elif '/resources/' in src:
            # Handle resource
            print('DEBUG:downloading from XNAT:'+src+' to '+dst_dir)
            result = self.download_xnat_resource(src, dst_dir)
            return result
        else:
            print('ERROR:invalid xnat path')
            return None

    def copy_local_input(self, src, input_name):
        dst_dir = os.path.join(self.input_dir, input_name)
        os.makedirs(dst_dir)

        if os.path.isdir(src):
            dst = os.path.join(dst_dir, os.path.basename(src))
            copytree(src, dst)
        elif os.path.isfile(src):
            dst = os.path.join(dst_dir, os.path.basename(src))
            copyfile(src, dst)
        else:
            print('ERROR:input does not exist:'+src)
            dst = None

        return dst

    def download_xnat_file(self, src, dst):
        result = None

        try:
            xnat = XnatUtils.get_interface(self.host, self.user, self.pwd)

            try:
                _res, _file = src.split('/files/')
                res = xnat.select(_res)
                result = res.file(_file).get(dst)
            except:
                print('ERROR:downloading from XNAT')
        except:
            print('ERROR:FAILED to get XNAT connection')
        finally:
            xnat.disconnect()

        return result

    def download_xnat_resource(self, src, dst):
        result = None

        try:
            xnat = XnatUtils.get_interface(self.host, self.user, self.pwd)
            try:
                res = xnat.select(src)
                res.get(dst, extract=True)
                result = dst
            except:
                print('ERROR:downloading from XNAT')
        except:
            print('ERROR:FAILED to get XNAT connection')
        finally:
            xnat.disconnect()

        return result

    def is_xnat_uri(self, uri):
        return uri.startswith('xnat:/')

    def parse_xnat_uri(self, src):
        src = src[len('xnat:/'):]
        src = src.replace(
            '{session}',
            '/projects/%s/subjects/%s/experiments/%s'
            % (self.xnat_project, self.xnat_subject, self.xnat_session))
        return src


# class to display time
class TimedWriter(object):
    '''
        Class to automatically write timed output message

        Args:
            name - Names to write with output (default=None)

        Examples:
            >>>a = Time_Writer()
            >>>a("this is a test")
            [00d 00h 00m 00s] this is a test
            >>>sleep(60)
            >>>a("this is a test")
            [00d 00h 01m 00s] this is a test

        Written by Andrew Plassard (Vanderbilt)
    '''
    def __init__(self, name=None, use_date=False):
        """
        Entry point of TimedWriter class

        :param name: Name to give the TimedWriter
        :return: None

        """
        self.start_time = time.localtime()
        self.name = name
        self.use_date = use_date

    def print_stderr_message(self, text):
        """
        Prints a timed message to stderr

        :param text: The text to print
        :return: None

        """
        self.print_timed_message(text, pipe=sys.stderr)

    def print_timed_message(self, text, pipe=sys.stdout):
        """
        Prints a timed message

        :param text: text to print
        :param pipe: pipe to write to. defaults to sys.stdout
        :return: None

        """
        msg = ""
        if self.name:
            msg = "[%s]" % self.name
        if self.use_date:
            now = datetime.now()
            msg = "%s[%s] %s" % (msg, now.strftime("%Y-%m-%d %H:%M:%S"), text)
        else:
            time_now = time.localtime()
            time_diff = time.mktime(time_now)-time.mktime(self.start_time)
            (days, res) = divmod(time_diff, 86400)
            (hours, res) = divmod(res, 3600)
            (mins, secs) = divmod(res, 60)
            msg = ("%s[%dd %02dh %02dm %02ds] %s"
                   % (msg, days, hours, mins, secs, text))
        print >> pipe, msg

    def __call__(self, text, pipe=sys.stdout):
        """
        Prints a timed message calling print_timed_message

        :param text: text to print
        :param pipe: pipe to write to. defaults to sys.stdout
        :return: None

        """
        self.print_timed_message(text, pipe=pipe)


# Functions
def get_default_value(variable, env_name, value):
    """
    Return the default value for the variable if arg not NULL else
     env variables defined by the args

    :param variable: variable name
    :param env_name: name of the environment variable
    :param value:    value given by the user
    :return: default value

    """
    if value:
        return value
    else:
        if env_name in os.environ and os.environ[env_name] != "":
            return os.environ[env_name]
        else:
            err = """%s not set by user.
To set it choose one of this solution:
Set the option --%s in the spider class
Set the environment variable %s
"""
            raise ValueError(err % (env_name, variable, env_name))


def get_pwd(host, user, pwd):
    """
    Return the password from env or ask user if the user was set

    :param host: xnat host
    :param user: xnat user
    :param pwd: password
    :return: default value

    """
    if pwd:
        return pwd
    else:
        if user:
            msg = "Enter the password for user '%s' on your XNAT -- %s :"
            return getpass.getpass(prompt=msg % (user, host))
        else:
            if "XNAT_PASS" in os.environ and os.environ["XNAT_PASS"] != "":
                return os.environ["XNAT_PASS"]
            else:
                err = "XNAT_PASS not set by user."
                err += "\n\t   Set the environment variable XNAT_PASS"
                raise ValueError(err)


def get_default_argparser(name, description):
    """
    Return default argparser arguments for all Spider

    :return: argparser obj

    """
    from argparse import ArgumentParser
    ap = ArgumentParser(prog=name, description=description)
    ap.add_argument('-p', dest='proj_label', help='Project Label',
                    required=True)
    ap.add_argument('-s', dest='subj_label', help='Subject Label',
                    required=True)
    ap.add_argument('-e', dest='sess_label', help='Session Label',
                    required=True)
    ap.add_argument('-d', dest='temp_dir', help='Temporary Directory',
                    required=True)
    ap.add_argument('--suffix', dest='suffix', default=None,
                    help='assessor suffix. default: None')
    ap.add_argument(
        '--host', dest='host', default=None,
        help='Set XNAT Host. Default: using env variable XNAT_HOST')
    ap.add_argument(
        '--user', dest='user', default=None,
        help='Set XNAT User. Default: using env variable XNAT_USER')
    ap.add_argument(
        '--skipfinish', action='store_true',
        help='Skip the finish step, so do not move files to upload queue')
    return ap


def get_session_argparser(name, description):
    """
    Return session argparser arguments for session Spider

    :return: argparser obj

    """
    ap = get_default_argparser(name, description)
    return ap


def get_scan_argparser(name, description):
    """
    Return scan argparser arguments for scan Spider

    :return: argparser obj

    """
    ap = get_default_argparser(name, description)
    ap.add_argument('-c', dest='scan_label', help='Scan label', required=True)
    return ap


def smaller_str(str_option, size=10, end=False):
    """Method to shorten a string into a smaller size.

    :param str_option: string to shorten
    :param size: size of the string to keep (default: 10 characters)
    :param end: keep the end of the string visible (default beginning)
    :return: shortened string
    """
    if len(str_option) > size:
        if end:
            return '...%s' % (str_option[-size:])
        else:
            return '%s...' % (str_option[:size])
    else:
        return str_option


def is_good_version(version):
    """
    Check the format of the version and return true if it's a proper format.
     Format: X.Y.Z see http://semver.org

    :param version: version given by the user
    :return: False if the version does not follow semantic
     versioning, true if it does.

    """
    vers = version.split('.')
    if len(vers) != 3:
        return False
    else:
        if not vers[0].isdigit() or \
           not vers[1].isdigit() or \
           not vers[2].isdigit():
            return False
    return True


def load_inputs(inputs_file):
    with open(inputs_file, 'Ur') as f:
        data = list(tuple(rec) for rec in csv.reader(f, delimiter=','))

    return data


def load_outputs(outputs_file):
    with open(outputs_file, 'Ur') as f:
        data = list(tuple(rec) for rec in csv.reader(f, delimiter=','))

    return data


def load_template(template_file):
    with open(template_file, "r") as f:
        data = f.read()

    return data


def use_time_writer(time_writer, msg):
    if not time_writer:
        print msg
    else:
        time_writer(msg)


# PDF Generator for spiders:
# Display images:
def plot_images(pdf_path, page_index, nii_images, title,
                image_labels, slices=None, cmap='gray',
                vmins=None, vmaxs=None, volume_ind=None,
                orient='ax', time_writer=None):
    """Plot list of images (3D-4D) on a figure (PDF page).

    plot_images_figure will create one pdf page with only images.
    Each image corresponds to one line with by default axial/sag/cor view
    of the mid slice. If you use slices, it will show different slices of
    the axial plan view. You can specify the cmap and the vmins/vmaxs if
    needed by using a dictionary with the index of each line (0, 1, ...).

    :param pdf_path: path to the pdf to save this figure to
    :param page_index: page index for PDF
    :param nii_images: python list of nifty images
    :param title: Title for the report page
    :param image_labels: list of titles for each images
        one per image in nii_images
    :param slices: dictionary of list of slices to display
        if None, display axial, coronal, sagital
    :param cmap: cmap to use to display images or dict
        of cmaps for each images with the indices as key
    :param vmins: define vmin for display (dict)
    :param vmaxs: define vmax for display (dict)
    :param volume_ind: if slices specified and 4D image given,
                       select volume
    :param orient: 'ax' or 'cor' or 'sag', default: 'sag'
    :param time_writer: function to print with time (default using print)
    :return: pdf path created

    E.g for two images:
    images = [imag1, image2]
    slices = {'0':[50, 80, 100, 130],
              '1':[150, 180, 200, 220]}
    labels = {'0': 'Label 1',
              '1': 'Label 2'}
    cmaps = {'0':'hot',
             '1': 'gray'}
    vmins = {'0':10,
             '1':20}
    vmaxs = {'0':100,
             '1':150}
    """
    plt.ioff()
    use_time_writer(time_writer, 'INFO: generating pdf page %d with images.'
                                 % page_index)
    fig = plt.figure(page_index, figsize=(7.5, 10))
    # Titles:
    if not isinstance(cmap, dict):
        default_cmap = cmap
        cmap = {}
    else:
        default_cmap = 'gray'
    if not isinstance(vmins, dict):
        use_time_writer(time_writer, "Warning: vmins wasn't a dictionary. \
Using default.")
        vmins = {}
    if not isinstance(vmaxs, dict):
        use_time_writer(time_writer, "Warning: vmaxs wasnt' a dictionary. \
Using default.")
        vmaxs = {}
    if isinstance(nii_images, str):
        nii_images = [nii_images]
    number_im = len(nii_images)

    if slices:
        use_time_writer(time_writer, 'INFO: showing different slices.')
    else:
        use_time_writer(time_writer, 'INFO: display different plan view \
(ax/sag/cor) of the mid slice.')
    for index, image in enumerate(nii_images):
        # Open niftis with nibabel
        f_img_ori = nib.load(image)
        # Reorient for display with python if fslswapdim exists:
        if True in [os.path.isfile(os.path.join(path, 'fslswapdim')) and
                    os.access(os.path.join(path, 'fslswapdim'), os.X_OK)
                    for path in os.environ["PATH"].split(os.pathsep)]:
            if image.endswith('.nii.gz'):
                ext = '.nii.gz'
            else:
                ext = '.nii'
            image_name = ('%s_reorient%s'
                          % (os.path.basename(image).split('.')[0], ext))
            image_reorient = os.path.join(os.path.dirname(image),
                                          image_name)
            qform = f_img_ori.header.get_qform()
            v = np.argmax(np.absolute(qform[0:3, 0:3]), axis=0)
            neg = {0: '', 1: '', 2: ''}
            if qform[v[0]][0] < 0:
                neg[0] = '-'
            if qform[v[1]][1] < 0:
                neg[1] = '-'
            if qform[v[2]][2] < 0:
                neg[2] = '-'
            args = '%s%s %s%s %s%s' % (neg[np.where(v == 0)[0][0]],
                                       FSLSWAP_VAL[np.where(v == 0)[0][0]],
                                       neg[np.where(v == 1)[0][0]],
                                       FSLSWAP_VAL[np.where(v == 1)[0][0]],
                                       neg[np.where(v == 2)[0][0]],
                                       FSLSWAP_VAL[np.where(v == 2)[0][0]])
            cmd = 'fslswapdim %s %s %s' % (image, args, image_reorient)
            use_time_writer(time_writer, 'INFO: command: %s' % cmd)
            os.system(cmd)

            if not os.path.exists(image_reorient) and \
               image_reorient.endswith('.nii'):
                image_reorient = '%s.gz' % image_reorient
            data = nib.load(image_reorient).get_data()
        else:
            data = f_img_ori.get_data()
        if len(data.shape) > 3:
            if isinstance(volume_ind, int):
                data = data[:, :, :, volume_ind]
            else:
                data = data[:, :, :, data.shape[3]/2]
        default_slices = [data.shape[2]/4, data.shape[2]/2,
                          3*data.shape[2]/4]
        default_label = 'Line %s' % index
        if slices:
            if not isinstance(slices, dict):
                use_time_writer(time_writer, "Warning: slices wasn't a \
dictionary. Using default.")
                slices = {}
            li_slices = slices.get(str(index), default_slices)
            slices_number = len(li_slices)
            for slice_ind, slice_value in enumerate(li_slices):
                ind = slices_number*index+slice_ind+1
                ax = fig.add_subplot(number_im, slices_number, ind)
                if orient == 'cor':
                    dslice = data[:, data.shape[1]/2, :]
                elif orient == 'ax':
                    dslice = data[:, :, data.shape[2]/2]
                else:
                    dslice = data[data.shape[0]/2, :, :]
                ax.imshow(np.rot90(np.transpose(dslice), 2),
                          cmap=cmap.get(str(index), default_cmap),
                          vmin=vmins.get(str(index), None),
                          vmax=vmaxs.get(str(index), None))
                ax.set_title('Slice %d' % slice_value, fontsize=7)
                ax.set_xticks([])
                ax.set_yticks([])
                ax.set_axis_off()
                if slice_ind == 0:
                    ax.set_ylabel(image_labels.get(str(index),
                                  default_label), fontsize=9)
        else:
            # Fix Orientation:
            dslice = []
            dslice_z = data[:, :, data.shape[2]/2]
            if dslice_z.shape[0] != dslice_z.shape[1]:
                dslice_z = imresize(dslice_z, (max(dslice_z.shape),
                                               max(dslice_z.shape)))
            dslice_y = data[:, data.shape[1]/2, :]
            if dslice_y.shape[0] != dslice_y.shape[1]:
                dslice_y = imresize(dslice_y, (max(dslice_y.shape),
                                               max(dslice_y.shape)))
            dslice_x = data[data.shape[0]/2, :, :]
            if dslice_x.shape[0] != dslice_x.shape[1]:
                dslice_x = imresize(dslice_x, (max(dslice_x.shape),
                                               max(dslice_x.shape)))

            dslice = [dslice_z, dslice_y, dslice_x]
            ax = fig.add_subplot(number_im, 3, 3*index+1)
            ax.imshow(np.rot90(np.transpose(dslice[0]), 2),
                      cmap=cmap.get(str(index), default_cmap),
                      vmin=vmins.get(str(index), None),
                      vmax=vmaxs.get(str(index), None))
            ax.set_title('Axial', fontsize=7)
            ax.set_ylabel(image_labels.get(str(index), default_label),
                          fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
            ax = fig.add_subplot(number_im, 3, 3*index+2)
            ax.imshow(np.rot90(np.transpose(dslice[1]), 2),
                      cmap=cmap.get(str(index), default_cmap),
                      vmin=vmins.get(str(index), None),
                      vmax=vmaxs.get(str(index), None))
            ax.set_title('Coronal', fontsize=7)
            ax.set_axis_off()
            ax = fig.add_subplot(number_im, 3, 3*index+3)
            ax.imshow(np.rot90(np.transpose(dslice[2]), 2),
                      cmap=cmap.get(str(index), default_cmap),
                      vmin=vmins.get(str(index), None),
                      vmax=vmaxs.get(str(index), None))
            ax.set_title('Sagittal', fontsize=7)
            ax.set_axis_off()

    fig.tight_layout()
    date = datetime.now()
    # Titles page
    plt.figtext(0.5, 0.985, '-- %s PDF report --' % title,
                horizontalalignment='center', fontsize=12)
    plt.figtext(0.5, 0.02, 'Date: %s -- page %d' % (str(date), page_index),
                horizontalalignment='center', fontsize=8)
    fig.savefig(pdf_path, transparent=True, orientation='portrait',
                dpi=100)
    plt.close(fig)
    return pdf_path


# Plot statistics in a table
def plot_stats(pdf_path, page_index, stats_dict, title,
               tables_number=3, columns_header=['Header', 'Value'],
               limit_size_text_column1=30, limit_size_text_column2=10,
               time_writer=None):
    """Generate pdf report of stats information from a csv/txt.

    plot_stats_page generate a pdf page displaying a dictionary
    of stats given to the function. Column 1 represents the key
    or header and the column 2 represents the value associated.
    You can rename the two column by using the args column1/2.
    There are three columns than can have 50 values max.

    :param pdf_path: path to the pdf to save this figure to
    :param page_index: page index for PDF
    :param stats_dict: python dictionary of key=value to display
    :param title: Title for the report page
    :param tables_number: number of columns to display (def:3)
    :param columns_header: list of header for the column
        default: header, value
    :param limit_size_text_column1: limit of text display in column 1
    :param limit_size_text_column2: limit of text display in column 2
    :param time_writer: function to print with time (default using print)
    :return: pdf path created
    """
    plt.ioff()
    use_time_writer(time_writer,
                    'INFO: generating pdf page %d with stats.' % page_index)

    cell_text = list()
    for key, value in stats_dict.items():
        txt = smaller_str(key.strip().replace('"', ''),
                          size=limit_size_text_column1)
        val = smaller_str(str(value),
                          size=limit_size_text_column2)
        cell_text.append([txt, "%s" % val])

    # Make the table
    fig = plt.figure(page_index, figsize=(7.5, 10))
    nb_stats = len(stats_dict.keys())
    for i in range(tables_number):
        ax = fig.add_subplot(1, tables_number, i+1)
        ax.xaxis.set_visible(False)
        ax.yaxis.set_visible(False)
        ax.axis('off')
        the_table = ax.table(
                cellText=cell_text[nb_stats/3*i:nb_stats/3*(i+1)],
                colColours=[(0.8, 0.4, 0.4), (1.0, 1.0, 0.4)],
                colLabels=columns_header,
                colWidths=[0.8, 0.32],
                loc='center',
                rowLoc='left',
                colLoc='left',
                cellLoc='left')

        the_table.auto_set_font_size(False)
        the_table.set_fontsize(6)

    # Set footer and title
    date = datetime.now()
    plt.figtext(0.5, 0.985, '-- %s PDF report --' % title,
                horizontalalignment='center', fontsize=12)
    plt.figtext(0.5, 0.02, 'Date: %s -- page %d' % (str(date), page_index),
                horizontalalignment='center', fontsize=8)
    fig.savefig(pdf_path, transparent=True, orientation='portrait',
                dpi=300)
    plt.close(fig)
    return pdf_path


# Merge PDF pages together using ghostscript 'gs'
def merge_pdfs(pdf_pages, pdf_final, time_writer=None):
    """Concatenate all pdf pages in the list into a final pdf.

    You can provide a list of pdf path or give a dictionary
    with each page specify by a number:
      pdf_pages = {'1': pdf_page1, '2': pdf_page2}

    :param pdf_pages: python list or dictionary of pdf page path
    :param pdf_final: final PDF path
    :param time_writer: function to print with time (default using print)
    :return: pdf path created
    """
    use_time_writer(time_writer, 'INFO: Concatenate all pdfs pages.')
    pages = ''
    if isinstance(pdf_pages, dict):
        for key in sorted(pdf_pages.iterkeys()):
            pages = '%s %s ' % (pages, pdf_pages[key])
    elif isinstance(pdf_pages, list):
        pages = ' '.join(pdf_pages)
    else:
        raise Exception('Wrong type for pdf_pages (list or dict).')
    cmd = 'gs -q -sPAPERSIZE=letter -dNOPAUSE -dBATCH \
-sDEVICE=pdfwrite -sOutputFile=%s %s' % (pdf_final, pages)
    use_time_writer(time_writer, 'INFO:saving final PDF: %s ' % cmd)
    os.system(cmd)
    return pdf_final
