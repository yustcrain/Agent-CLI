import json
import sys

def main():
    values = [float(x) for x in sys.argv[1:]]
    print({"count": len(values), "sum": sum(values)})

if __name__ == "__main__":
    main()
