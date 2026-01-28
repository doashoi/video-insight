
import lark_oapi.api.drive.v1 as drive_v1
import inspect

print("Searching for Transfer related classes in drive.v1:")
for name, obj in inspect.getmembers(drive_v1):
    if "Transfer" in name:
        print(name)
