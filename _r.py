import sys, os; sys.path.insert(0, os.getcwd())
sys.argv=['validate.py']+sys.argv[1:]
import validate; sys.exit(validate.main())
