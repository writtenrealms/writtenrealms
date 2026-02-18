#!/bin/sh

# Check if RUN_TESTS is defined
if [ -n "$RUN_TESTS" ]; then
    echo "Running tests..."
    case $RUN_TESTS in
        wr2|backend|forge|all)
            (cd backend && python manage.py test wr2_tests --settings=config.settings.testing)
            ;;
        *)
            # Optionally handle unknown values
            echo "Unknown RUN_TESTS value: $RUN_TESTS"
            exit 1
            ;;
    esac
else
    # If RUN_TESTS is not defined, execute the default command
    exec "$@"
fi
