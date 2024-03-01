import os
import importlib

dir_path = os.path.dirname(__file__)
attachment_strategies_dict = {}
for file in os.listdir(dir_path):
    if file.endswith('.py') and file != '__init__.py':
        module_name = file[:-3]  # we only care about name of the file, not extension
        module = importlib.import_module(f'.{module_name}', package=__name__)
    attachment_strategies_dict[module.name]=module.handler
# dynamic import from attachment_strategies directory, this will be marked as unresolved reference by most linters

