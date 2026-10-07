import configparser
import os
import shutil


def task_func(config_file_path, archieve_dir ='/home/user/archive'):
    """
    Archive a specified project directory into a ZIP file based on the configuration specified in a config file.
    
    This function reads a configuration file to determine the project directory and archives this directory into a ZIP file.
    The ZIP file's name will be the project directory's basename, stored in the specified archive directory.
    
    Configuration File Format:
    [Project]
    directory=path_to_project_directory
    
    Parameters:
    - config_file_path (str): Path to the configuration file. The file must exist and be readable.
    - archive_dir (str, optional): Path to the directory where the ZIP archive will be stored. Defaults to '/home/user/archive'.
    
    Returns:
    - bool: True if the ZIP archive is successfully created, otherwise an exception is raised.
    
    Requirements:
    - configparse
    - os
    - shutil

    Raises:
    - FileNotFoundError: If the `config_file_path` does not exist or the specified project directory does not exist.
    - Exception: If the ZIP archive cannot be created.
    
    Example:
    >>> task_func("/path/to/config.ini")
    True
    """
    config = configparser.ConfigParser()
    config.read(config_file_path)

    project_dir = config.get('Project', 'directory')

    if not os.path.isdir(project_dir):
        raise FileNotFoundError(f'Directory {project_dir} does not exist.')

    archive_file = f'{archieve_dir}/{os.path.basename(project_dir)}.zip'
    
    # Using shutil to create the zip archive
    shutil.make_archive(base_name=os.path.splitext(archive_file)[0], format='zip', root_dir=project_dir)

    if not os.path.isfile(archive_file):
        raise Exception(f"Failed to create archive {archive_file}")

    return True