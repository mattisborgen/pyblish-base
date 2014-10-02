"""Entry-point of Pyblish

Attributes:
    TAB: Number of spaces for a tab
    LOG_TEMPATE: Template used for logging coming from
        plug-ins
    SCREEN_WIDTH: Default width at which logging and printing
        will (attempt to) restrain to.
    logging_handlers: Record of handlers at the start of
        importing this module. This module will modify the
        currently handlers and restore then once finished.
    log: Current logger
    intro_message: Message printed upon initiating a publish.

"""

from __future__ import absolute_import

# Standard library
import time
import logging

# Local library
import pyblish.api

TAB = "    "
LOG_TEMPATE = "{tab}%(levelname)-8s %(message)s".format(tab=TAB)
SCREEN_WIDTH = 80

logging_handlers = logging.getLogger().handlers[:]
log = logging.getLogger('pyblish.main')

intro_message = """
%s
pyblish version {version}
%s

User Configuration @ {user_path}

Available plugin paths:
{paths}

Available plugins:
{plugins}
""" % ("-" * SCREEN_WIDTH, "-" * SCREEN_WIDTH)

__all__ = ['select',
           'validate',
           'extract',
           'conform',
           'publish',
           'publish_all']


def _format_paths(paths):
    """Return paths at one new each"""
    message = ''
    for path in paths:
        message += "{0}- {1}\n".format(TAB, path)
    return message[:-1]  # Discard last newline


def _format_plugins(plugins):
    message = ''
    for plugin in sorted(plugins, key=lambda p: p.__name__):
        line = "{tab}- {plug}".format(
            tab=TAB, plug=plugin.__name__)

        if hasattr(plugin, 'families'):
            line = line.ljust(50) + " "
            for family in plugin.families:
                line += "%s, " % family
            line = line[:-2]

        line += "\n"

        message += line

    return message[:-1]


def publish(context=None,
            auto_repair=False,
            logging_level=logging.INFO,
            **kwargs):
    """Publish everything

    This function will process all available plugins of the
    currently running host, publishing anything picked up
    during selection.

    Arguments:
        context (pyblish.api.Context): Optional Context.
            Defaults to creating a new context each time.
        types (list): Optional list of strings with names of types
            to perform. Default is to perform all types.
        delay (float): Add artificial delay to the processing
            of each plug-in. Used in debugging.
        logging_level (logging level): Optional level with which
            to log messages. Default is logging.INFO.

    Usage:
        >> publish()
        >> publish(context=Context())

    """

    # Hidden argument
    _orders = kwargs.pop('orders', None) or (0, 1, 2, 3)
    assert not kwargs  # There are no more arguments

    obj = Publish(context)

    obj.logging_level = logging_level
    obj.orders = _orders
    obj.repair = auto_repair
    obj.process()

    return obj.context


def validate_all(*args, **kwargs):
    if not 'orders' in kwargs:
        kwargs['orders'] = (0, 1)
    return publish(*args, **kwargs)


def select(*args, **kwargs):
    """Convenience function for selection"""
    if not 'orders' in kwargs:
        kwargs['orders'] = (0,)
    return publish(*args, **kwargs)


def validate(*args, **kwargs):
    """Convenience function for validation"""
    if not 'orders' in kwargs:
        kwargs['orders'] = (1,)
    return publish(*args, **kwargs)


def extract(*args, **kwargs):
    """Convenience function for extraction"""
    if not 'orders' in kwargs:
        kwargs['orders'] = (2,)
    return publish(*args, **kwargs)


def conform(*args, **kwargs):
    """Convenience function for conform"""
    if not 'orders' in kwargs:
        kwargs['orders'] = (3,)
    return publish(*args, **kwargs)


class Publish(object):
    SCREEN_WIDTH = 80
    LOG_TEMPATE = "    %(levelname)-8s %(message)s"
    TAB = "    "

    log = logging.getLogger()
    logging_level = logging.INFO

    @property
    def duration(self):
        return "%.2f" % (self._time['end'] - self._time['start'])

    def __init__(self, context=None):
        if context is None:
            pyblish.api.Context.delete()
            context = pyblish.api.Context()

        self.context = context
        self.orders = (0, 1, 2, 3)
        self.repair = False

        self._plugins = pyblish.plugin.Plugins()
        self._conf = pyblish.api.config
        self._time = {'start': None, 'end': None}
        self._errors = list()

        self._plugins.discover()

    def process(self):
        """Process all instances within the given context"""
        self._time['start'] = time.time()
        self._log_intro()

        log_summary = False

        try:
            for order in self.orders:
                self.process_order(order)

        except pyblish.api.NoInstancesError as exc:
            self.log.warning("Cancelled due to not finding any instances")

        except pyblish.api.SelectionError:
            self.log.error("Selection failed")

        except pyblish.api.ValidationError as exc:
            self.log.error("Validation failed")

            print  # newline
            print "These validations failed:"
            for error in exc.errors:
                print "{tab}- \"{inst}\": {exc} ({plug})".format(
                    inst=error.instance,
                    tab=TAB,
                    exc=error,
                    plug=error.plugin.__name__)

        except Exception as exc:
            self.log.error("Unhandled exception: %s" % exc)

        else:
            log_summary = True

        # Clear context
        pyblish.api.Context.delete()
        self._time['end'] = time.time()

        print  # newline
        print "-" * 80

        self._log_time()

        # Revert to a simpler handler
        logging.getLogger().handlers[:] = []

        formatter = logging.Formatter("%(levelname)s - %(message)s")

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logging.getLogger().addHandler(stream_handler)

        if log_summary:
            self._log_summary()
        self._log_success()

        self._reset_log()

    def process_order(self, order):
        """Process context using plug-ins with the specified `order`

        Arguments:
            order (int): Order of plug-ins with which to process context.

        Raises:
            pyblish.api.SelectionError: When selection fails
            pyblish.api.ValidationError: When validation fails

        """

        if order != 0 and not self.context:
            # If there aren't any instances after selection,
            # there is no point in going on.
            raise pyblish.api.NoInstancesError

        order_errors = list()
        for plugin in self._plugins:
            if plugin.order != order:
                continue

            plugin_errors = self.process_plugin(plugin)
            order_errors.extend(plugin_errors)

            if not plugin_errors:
                continue

            # Before proceeding with extraction, ensure
            # that there are no failed validators.
            self.log.warning("There were errors:")
            for error in plugin_errors:
                self._log_error(error.instance, error)

        if not order_errors:
            return

        # If the error occurred during selection or validation,
        # we don't want to continue.
        if order == 0:
            raise pyblish.api.SelectionError

        if order == 1:
            exception = pyblish.api.ValidationError
            exception.errors = order_errors
            raise exception

    def process_plugin(self, plugin):
        """Process context using a single plugin

        Arguments:
            plugin (Plugin): Plug-in used to process context

        Returns:
            List of errors occurred for `plugin`

        """

        self._log_plugin(plugin)

        # Initialise pretty-printing for plug-ins
        self._init_log()

        errors = list()
        for instance, error in plugin().process(self.context):
            if error is None:
                continue

            repaired = False
            if plugin.order == 1 and self.repair:
                repaired = self._repair(plugin, instance)

            if not repaired:
                errors.append(error)

                # Inject data for logging
                error.instance = instance
                error.plugin = plugin

                # Store global reference for self._report()
                self._errors.append(error)

        return errors

    def _repair(self, plugin, instance):
        if hasattr(plugin, 'repair_instance'):
            self.log.warning("There were errors, attempting "
                             "to auto-repair..")
            try:
                plugin().repair_instance(instance)

            except Exception as err:
                self.log.warning("Could not auto-repair..")
                self.log.warning(err)

            else:
                self.log.info("Auto-repair successful")
                return True

        return False

    def _init_log(self):
        self.log = logging.getLogger()
        self.log.handlers[:] = []

        formatter = logging.Formatter(self.LOG_TEMPATE)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.log.addHandler(stream_handler)

        self.log.setLevel(self.logging_level)

    def _reset_log(self):
        self.log = logging.getLogger()
        self.log.handlers[:] = logging_handlers[:]
        self.log.setLevel(logging.INFO)

    def _log_plugin(self, plugin):
        if hasattr(plugin, 'name'):
            name = "%s (%s)" % (plugin.__name__, plugin.name)
        else:
            name = plugin.__name__

        print "{plugin}...".format(
            tab=self.TAB,
            plugin=name)

    def _log_intro(self):
        message = intro_message.format(
            version=pyblish.__version__,
            user_path=(self._conf['USERCONFIGPATH']
                       if self._conf.user else "None"),
            paths=_format_paths(self._plugins.paths),
            plugins=_format_plugins(self._plugins))

        message += "\n{line}\nProcessing\n".format(line="-" * 80)
        print message

    def _log_error(self, instance, error):
        """Format outputted error message

        Including:
            - Instance involved in error
            - File name in which the error occurred
            - Function/method of error
            - Line number of error

        Arguments:
            instance (pyblish.api.Instance): Instance involved in error
            error (Exception): Error to format

        Returns:
            Error as pretty-formatted string

        """

        traceback = getattr(error, 'traceback', None)

        if traceback:
            fname, line_number, func, exc = traceback
            traceback = ("(Line {line} in \"{file}\" "
                         "@ \"{func}\")".format(line=line_number,
                                                file=fname,
                                                func=func))

        self.log.error("{tab}{i}: {e} {tb}".format(
            tab=self.TAB,
            i=instance,
            e=error,
            tb=traceback if traceback else ''))

    def _log_time(self):
        """Return time-taken message"""
        message = 'Time taken: %s' % self.duration
        print message.rjust(SCREEN_WIDTH)

    def _log_success(self):
        """Log a success message"""
        processed_instances = list()

        for instance in self.context:
            if not instance.data('__is_processed__'):
                continue
            processed_instances.append(instance)

        if self.context and not processed_instances:
            self.log.warning("Instances were found, but none were processed")
            return

        if not self.context:
            self.log.warning("No instances were found")
            return

        status = "successfully without errors"
        if self._errors:
            status = "with errors"

        num_processed_instances = len(processed_instances)
        (self.log.warning if self._errors else self.log.info)(
            "Processed {num} instance{s} {status} "
            "in {seconds}s".format(
                num=num_processed_instances,
                s="s" if num_processed_instances > 1 else "",
                status=status,
                seconds=self.duration))

    def _log_summary(self):
        """Layout summary for `context`"""
        message = "Summary:\n"

        for instance in self.context:
            is_processed = instance.data('__is_processed__')
            processed_by = instance.data('__processed_by__')
            commit_dir = instance.data('commit_dir')
            conform_dirs = instance.data('conform_dirs')

            _message = "{tab}- \"{inst}\" ".format(
                tab=self.TAB,
                inst=instance)

            _message += "processed by:"

            if is_processed:
                for _plugin in processed_by or list():
                    _message += " \"%s\"," % _plugin.__name__
                _message = _message[:-1]

            else:
                _message += " None"

            message += _message + "\n"

            if commit_dir:
                message += "{tab}Committed to: {dir}".format(
                    tab=self.TAB*2, dir=commit_dir) + "\n"

            if conform_dirs:
                message += "{tab}Conformed to: {dir}".format(
                    tab=self.TAB*2, dir=", ".join(conform_dirs)) + "\n"

        print  # newline
        print message


# For backwards compatibility
publish_all = publish
