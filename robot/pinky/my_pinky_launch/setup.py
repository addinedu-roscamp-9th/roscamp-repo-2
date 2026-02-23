import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'my_pinky_launch'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # ✅ launch 파일 설치 (상대경로만!)
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='pinky',
    maintainer_email='pinky@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={'test': ['pytest']},
    entry_points={'console_scripts': []},
)
