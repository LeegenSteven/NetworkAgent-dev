#!/usr/bin/env python3
# Copyright 2024-2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import argparse
import sys
import os
import logging
import importlib.util
import importlib

# Add the test directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the test class
from test_engineer_agent import TestEngineerAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s::%(levelname)s::%(name)s::%(filename)s::%(lineno)d::%(message)s"
)
logger = logging.getLogger(__name__)

def run_tests(test_names=None, address=None):
    """
    Run the specified tests or all tests if none specified.
    
    Args:
        test_names: List of test method names to run, or None to run all tests
        address: Address of the Engineer Agent server
    
    Returns:
        True if all tests passed, False otherwise
    """
    # Set the Engineer Agent address if provided
    if address:
        os.environ["ENGINEER_ADDRESS"] = address
        
    # Create a test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    if test_names:
        # Add specific tests
        for test_name in test_names:
            try:
                if '.' in test_name:
                    # Format: TestClass.test_method
                    class_name, method_name = test_name.split('.')
                    test_case = loader.loadTestsFromName(f"test_engineer_agent.{class_name}.{method_name}")
                else:
                    # Format: test_method (assumes TestEngineerAgent class)
                    test_case = loader.loadTestsFromName(f"test_engineer_agent.TestEngineerAgent.{test_name}")
                suite.addTest(test_case)
            except Exception as e:
                logger.error(f"Error loading test {test_name}: {str(e)}")
                return False
    else:
        # Add all tests from TestEngineerAgent
        suite.addTest(loader.loadTestsFromTestCase(TestEngineerAgent))
    
    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()

def main():
    """Parse command line arguments and run tests."""
    parser = argparse.ArgumentParser(description="Run Engineer Agent tests")
    parser.add_argument(
        "--address", 
        type=str, 
        help="Address of the Engineer Agent server (default: http://localhost:8081 or ENGINEER_ADDRESS env var)"
    )
    parser.add_argument(
        "tests", 
        nargs="*", 
        help="Specific tests to run (e.g., test_send_task or TestEngineerAgent.test_send_task)"
    )
    
    args = parser.parse_args()
    
    # Run the tests
    success = run_tests(args.tests, args.address)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
