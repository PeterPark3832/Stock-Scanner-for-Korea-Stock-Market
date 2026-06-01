"""pytest root — ensures project root is on sys.path."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
