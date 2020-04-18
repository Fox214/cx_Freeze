from distutils.core import Command, DistutilsFileError
import os
import plistlib
import stat
import subprocess

from cx_Freeze.common import normalize_to_list

__all__ = ["bdist_dmg", "bdist_mac"]


class bdist_dmg(Command):
    description = "create a Mac DMG disk image containing the Mac " \
        "application bundle"
    user_options = [
        ('volume-label=', None, 'Volume label of the DMG disk image'),
        ('applications-shortcut=', None, 'Boolean for whether to include '
            'shortcut to Applications in the DMG disk image'),
    ]

    def initialize_options(self):
        self.volume_label = self.distribution.get_fullname()
        self.applications_shortcut = False

    def finalize_options(self):
        pass

    def buildDMG(self):
        # Remove DMG if it already exists
        if os.path.exists(self.dmgName):
            os.unlink(self.dmgName)

        createargs = [
            'hdiutil', 'create', '-fs', 'HFSX', '-format', 'UDZO',
            self.dmgName, '-imagekey', 'zlib-level=9', '-srcfolder',
            self.bundleDir, '-volname', self.volume_label
        ]

        if self.applications_shortcut:
            scriptargs = [
                'osascript', '-e', 'tell application "Finder" to make alias \
                file to POSIX file "/Applications" at POSIX file "%s"' %
                os.path.realpath(self.buildDir)
            ]

            if os.spawnvp(os.P_WAIT, 'osascript', scriptargs) != 0:
                raise OSError('creation of Applications shortcut failed')

            createargs.append('-srcfolder')
            createargs.append(self.buildDir + '/Applications')

        # Create the dmg
        if os.spawnvp(os.P_WAIT, 'hdiutil', createargs) != 0:
            raise OSError('creation of the dmg failed')

    def run(self):
        # Create the application bundle
        self.run_command('bdist_mac')

        # Find the location of the application bundle and the build dir
        self.bundleDir = self.get_finalized_command('bdist_mac').bundleDir
        self.buildDir = self.get_finalized_command('build').build_base

        # Set the file name of the DMG to be built
        self.dmgName = os.path.join(self.buildDir,
                                    self.volume_label + '.dmg')

        self.execute(self.buildDMG, ())


class bdist_mac(Command):
    description = "create a Mac application bundle"

    user_options = [
        ('iconfile=', None, 'Path to an icns icon file for the application.'),
        ('qt-menu-nib=', None, 'Location of qt_menu.nib folder for Qt '
            'applications. Will be auto-detected by default.'),
        ('bundle-name=', None, 'File name for the bundle application '
            'without the .app extension.'),
        ('custom-info-plist=', None, 'File to be used as the Info.plist in '
            'the app bundle. A basic one will be generated by default.'),
        ('include-frameworks=', None, 'A comma separated list of Framework '
            'directories to include in the app bundle.'),
        ('include-resources=', None, 'A list of tuples of additional ' \
            'files to include in the app bundle\'s resources directory, with ' \
            'the first element being the source, and second the destination ' \
            'file or directory name.'),
        ('codesign-identity=', None, 'The identity of the key to be used to '
            'sign the app bundle.'),
        ('codesign-entitlements=', None, 'The path to an entitlements file '
            'to use for your application\'s code signature.'),
        ('codesign-deep=', None, 'Boolean for whether to codesign using the ' \
            '--deep option.'),
        ('codesign-resource-rules', None, 'Plist file to be passed to ' \
            'codesign\'s --resource-rules option.'),
        ('absolute-reference-path=', None, 'Path to use for all referenced ' \
            'libraries instead of @executable_path.'),
        ('rpath-lib-folder', None, 'replace @rpath with given folder for any files')
    ]

    def initialize_options(self):
        self.iconfile = None
        self.qt_menu_nib = False
        self.bundle_name = self.distribution.get_fullname()
        self.custom_info_plist = None
        self.include_frameworks = []
        self.include_resources = []
        self.codesign_identity = None
        self.codesign_entitlements = None
        self.codesign_deep = None
        self.codesign_resource_rules = None
        self.absolute_reference_path = None
        self.rpath_lib_folder = None

    def finalize_options(self):
        self.include_frameworks = normalize_to_list(self.include_frameworks)

    def create_plist(self):
        """Create the Contents/Info.plist file"""
        # Use custom plist if supplied, otherwise create a simple default.
        if self.custom_info_plist:
            contents = plistlib.readPlist(self.custom_info_plist)
        else:
            contents = {
                'CFBundleIconFile': 'icon.icns',
                'CFBundleDevelopmentRegion': 'English',
                'CFBundleIdentifier': self.bundle_name,
            }

        # Ensure CFBundleExecutable is set correctly
        contents['CFBundleExecutable'] = self.bundle_executable

        plist = open(os.path.join(self.contentsDir, 'Info.plist'), 'wb')
        plistlib.writePlist(contents, plist)
        plist.close()

    def setAbsoluteReferencePaths(self, path=None):
        """
        For all files in Contents/MacOS, set their linked library paths to be
        absolute paths using the given path instead of @executable_path.
        """
        if not path:
            path = self.absolute_reference_path

        files = os.listdir(self.binDir)

        for filename in files:
            filename = os.path.join(self.binDir, filename)

            # Skip some file types
            if filename[-1:] in ('txt', 'zip') or os.path.isdir(filename):
                continue

            otool = subprocess.Popen(('otool',  '-L', filename),
                                     stdout=subprocess.PIPE)

            for line in otool.stdout.readlines()[1:]:
                lib = line.decode('utf-8').lstrip('\t').split(' (compat')[0]

                if lib.startswith('@executable_path'):
                    replacement = lib.replace('@executable_path', path)

                    path, name = os.path.split(replacement)

                    # see if we provide the referenced file;
                    # if so, change the reference
                    if name in files:
                        subprocess.call((
                            'install_name_tool', '-change', lib, replacement,
                            filename))

    def setRelativeReferencePaths(self):
        """ Create a list of all the Mach-O binaries in Contents/MacOS.
            Then, check if they contain references to other files in
            that dir. If so, make those references relative. """
        files = []
        for root, dirs, dir_files in os.walk(self.binDir):
            for f in dir_files:
                p = subprocess.Popen(("file", os.path.join(root, f)), stdout=subprocess.PIPE)
                if b"Mach-O" in p.stdout.readline():
                    files.append(os.path.join(root, f).replace(self.binDir + "/", ""))

        for fileName in files:

            filePath = os.path.join(self.binDir, fileName)

            # ensure write permissions
            mode = os.stat(filePath).st_mode
            if not (mode & stat.S_IWUSR):
                os.chmod(filePath, mode | stat.S_IWUSR)

            # let the file itself know its place
            subprocess.call(('install_name_tool', '-id',
                             '@executable_path/' + fileName, filePath))

            # find the references: call otool -L on the file
            otool = subprocess.Popen(('otool', '-L', filePath),
                                     stdout=subprocess.PIPE)
            references = otool.stdout.readlines()[1:]

            for reference in references:

                # find the actual referenced file name
                referencedFile = reference.decode().strip().split()[0]

                if referencedFile.startswith('@executable_path'):
                    # the referencedFile is already a relative path (to the executable)
                    continue

                if self.rpath_lib_folder is not None:
                    referencedFile = str(referencedFile).replace("@rpath", self.rpath_lib_folder)

                # the output of otool on archive contain self referencing
                # content inside parantheses.
                if not os.path.exists(referencedFile):
                    print("skip unknown file {} ".format(referencedFile))
                    continue

                path, name = os.path.split(referencedFile)

                #some referenced files have not previously been copied to the
                #executable directory - the assumption is that you don't need
                #to copy anything fro /usr or /System, just from folders like
                #/opt this fix should probably be elsewhere though
                if (name not in files and not path.startswith('/usr') and not
                        path.startswith('/System')):
                    print(referencedFile)
                    try:
                        self.copy_file(referencedFile, os.path.join(self.binDir, name))
                    except DistutilsFileError as e:
                        print("issue copying {} to {} error {} skipping".format(referencedFile, os.path.join(self.binDir, name), e))
                    else:
                        files.append(name)

                # see if we provide the referenced file;
                # if so, change the reference
                if name in files:
                    newReference = '@executable_path/' + name
                    subprocess.call(('install_name_tool', '-change',
                                    referencedFile, newReference, filePath))

    def find_qt_menu_nib(self):
        """Returns a location of a qt_menu.nib folder, or None if this is not
           a Qt application."""
        if self.qt_menu_nib:
            return self.qt_menu_nib
        elif any(n.startswith("PyQt4.QtCore")
                 for n in os.listdir(self.binDir)):
            from PyQt4 import QtCore
        elif any(n.startswith("PySide.QtCore")
                 for n in os.listdir(self.binDir)):
            from PySide import QtCore
        else:
            return None

        libpath = str(QtCore.QLibraryInfo.location(
                      QtCore.QLibraryInfo.LibrariesPath))
        for subpath in ['QtGui.framework/Resources/qt_menu.nib',
                        'Resources/qt_menu.nib']:
            path = os.path.join(libpath, subpath)
            if os.path.exists(path):
                return path

        # Last resort: fixed paths (macports)
        for path in ['/opt/local/Library/Frameworks/QtGui.framework/Versions/'
                     '4/Resources/qt_menu.nib']:
            if os.path.exists(path):
                return path

        print ("Could not find qt_menu.nib")
        raise IOError("Could not find qt_menu.nib")

    def prepare_qt_app(self):
        """Add resource files for a Qt application. Should do nothing if the
           application does not use QtCore."""
        nib_locn = self.find_qt_menu_nib()
        if nib_locn is None:
            return

        # Copy qt_menu.nib
        self.copy_tree(nib_locn, os.path.join(self.resourcesDir,
                       'qt_menu.nib'))

        # qt.conf needs to exist, but needn't have any content
        f = open(os.path.join(self.resourcesDir, 'qt.conf'), "w")
        f.close()

    def run(self):
        self.run_command('build')
        build = self.get_finalized_command('build')

        # Define the paths within the application bundle
        self.bundleDir = os.path.join(build.build_base,
                                      self.bundle_name + ".app")
        self.contentsDir = os.path.join(self.bundleDir, 'Contents')
        self.resourcesDir = os.path.join(self.contentsDir, 'Resources')
        self.binDir = os.path.join(self.contentsDir, 'MacOS')
        self.frameworksDir = os.path.join(self.contentsDir, 'Frameworks')

        #Find the executable name
        executable = self.distribution.executables[0].targetName
        _, self.bundle_executable = os.path.split(executable)

        # Build the app directory structure
        self.mkpath(self.resourcesDir)
        self.mkpath(self.binDir)
        self.mkpath(self.frameworksDir)

        self.copy_tree(build.build_exe, self.binDir)

        # Copy the icon
        if self.iconfile:
            self.copy_file(self.iconfile, os.path.join(self.resourcesDir,
                                                       'icon.icns'))

        # Copy in Frameworks
        for framework in self.include_frameworks:
            self.copy_tree(framework, self.frameworksDir + '/' +
                           os.path.basename(framework))

        # Copy in Resources
        for resource, destination in self.include_resources:
            if os.path.isdir(resource):
                self.copy_tree(resource, self.resourcesDir + '/' +
                               destination)
            else:
                self.copy_file(resource, self.resourcesDir + '/' +
                               destination)

        # Create the Info.plist file
        self.execute(self.create_plist, ())

        # Make all references to libraries relative
        self.execute(self.setRelativeReferencePaths, ())

        # Make library references absolute if enabled
        if self.absolute_reference_path:
            self.execute(self.setAbsoluteReferencePaths, ())

        # For a Qt application, run some tweaks
        self.execute(self.prepare_qt_app, ())

        # Sign the app bundle if a key is specified
        if self.codesign_identity:
            signargs = [
                'codesign', '-s', self.codesign_identity
            ]

            if self.codesign_entitlements:
                signargs.append('--entitlements')
                signargs.append(self.codesign_entitlements)

            if self.codesign_deep:
                signargs.insert(1, '--deep')

            if self.codesign_resource_rules:
                signargs.insert(1, '--resource-rules=' +
                                self.codesign_resource_rules)

            signargs.append(self.bundleDir)

            if os.spawnvp(os.P_WAIT, 'codesign', signargs) != 0:
                raise OSError('Code signing of app bundle failed')
