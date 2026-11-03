"""Private Windows child containment. Import is inert; no global OS handles.

One unnamed, non-inheritable Job per child; active limit one, no breakaway.
The child is created suspended, assigned and checked before its thread resumes.
The injected API exists for pure tests, not an operational configuration option.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import os
import struct
import subprocess
import time


class OwnershipError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise OwnershipError(code)


class BasicLimits(ctypes.Structure):
    _fields_ = [('per_process', ctypes.c_longlong), ('per_job', ctypes.c_longlong),
        ('flags', W.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
        ('active_limit', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IoCounters)] + [(name, ctypes.c_size_t) for name in
        ('process_memory', 'job_memory', 'peak_process', 'peak_job')]


def parse_members(raw, pointer_size):
    require(pointer_size in (4, 8) and len(raw) >= 8, 'job_member_buffer')
    assigned, listed = struct.unpack_from('<II', raw)
    require(assigned == listed and listed <= 64 and 8 + listed * pointer_size <= len(raw), 'job_member_count')
    members = tuple(struct.unpack_from('<' + ('Q' if pointer_size == 8 else 'I') * listed, raw, 8))
    require(all(pid > 0 for pid in members) and len(set(members)) == len(members), 'job_member_identity')
    return members


class WindowsApi:
    def __init__(self):
        require(os.name == 'nt', 'windows_owned_job_required')
        import _winapi
        self.winapi = _winapi
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            'SetInformationJobObject': ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
            'QueryInformationJobObject': ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)], W.BOOL),
            'AssignProcessToJobObject': ([W.HANDLE, W.HANDLE], W.BOOL),
            'IsProcessInJob': ([W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)], W.BOOL),
            'TerminateJobObject': ([W.HANDLE, W.UINT], W.BOOL),
            'ResumeThread': ([W.HANDLE], W.DWORD),
            'GetProcessId': ([W.HANDLE], W.DWORD),
            'OpenProcess': ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            'QueryFullProcessImageNameW': ([W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)], W.BOOL),
            'GetProcessTimes': ([W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4, W.BOOL),
            'CloseHandle': ([W.HANDLE], W.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result

    def create_job(self):
        job = self.kernel.CreateJobObjectW(None, None)
        require(job, 'job_create_failed')
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000 | 0x8  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS
            limits.basic.active_limit = 1
            require(self.kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)), 'job_limit_failed')
            confirmed = ExtendedLimits()
            returned = W.DWORD()
            require(self.kernel.QueryInformationJobObject(job, 9, ctypes.byref(confirmed), ctypes.sizeof(confirmed), ctypes.byref(returned)), 'job_limit_query_failed')
            require(confirmed.basic.flags == limits.basic.flags and confirmed.basic.active_limit == 1, 'job_limit_differs')
            return job
        except BaseException:
            self.close(job)
            raise

    def create_suspended(self, argv, env, cwd):
        import msvcrt
        require(argv and os.path.isabs(argv[0]), 'absolute_executable_required')
        read_fd = write_fd = null_fd = None
        process = thread = None
        stream = None
        primary = None
        errors = []
        try:
            read_fd, write_fd = os.pipe()
            null_fd = os.open(os.devnull, os.O_RDONLY)
            output = msvcrt.get_osfhandle(write_fd)
            input_handle = msvcrt.get_osfhandle(null_fd)
            os.set_handle_inheritable(output, True)
            os.set_handle_inheritable(input_handle, True)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESTDHANDLES
            startup.hStdInput = input_handle
            startup.hStdOutput = startup.hStdError = output
            startup.lpAttributeList = {'handle_list': [output, input_handle]}
            process, thread, pid, _ = self.winapi.CreateProcess(
                argv[0], subprocess.list2cmdline(argv), None, None, True,
                0x4 | 0x8, env, cwd, startup)  # SUSPENDED | DETACHED_PROCESS
            stream = os.fdopen(read_fd, 'rb', buffering=0)
            read_fd = None
        except BaseException as error:
            primary = error
        # Complete ALL parent descriptor transfers before returning ownership.
        # A close may fail after closing the descriptor: never retry its numeric
        # value (which another thread could reuse). Record uncertain cleanup.
        for descriptor in (read_fd, write_fd, null_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    errors.append('parent_descriptor_close_failed')
                    if primary is None:
                        primary = error
        if primary is None:
            return process, thread, pid, stream
        # A failed return cannot transfer this suspended, unassigned process to
        # OwnedProcess. Settle it here even when the failure was finalization.
        if process is not None:
            try:
                self.winapi.TerminateProcess(process, 1)
            except BaseException:
                errors.append('suspended_process_terminate_failed')
            try:
                require(self.winapi.WaitForSingleObject(process, 2000) == 0, 'suspended_cleanup_timeout')
            except BaseException:
                errors.append('suspended_process_wait_failed')
        for handle in (thread, process):
            if handle is not None:
                try:
                    self.close(handle)
                except BaseException:
                    errors.append('suspended_handle_close_failed')
        if stream is not None:
            try:
                stream.close()
            except BaseException:
                errors.append('suspended_stream_close_failed')
        try:
            primary.suspended_cleanup_errors = errors
        except Exception:
            pass
        raise primary

    def assign(self, job, process):
        require(self.kernel.AssignProcessToJobObject(job, process), 'job_assignment_failed')

    def in_job(self, job, process):
        value = W.BOOL()
        require(self.kernel.IsProcessInJob(process, job, ctypes.byref(value)), 'job_membership_query_failed')
        return bool(value.value)

    def identity(self, process):
        times = [W.FILETIME() for _ in range(4)]
        require(self.kernel.GetProcessTimes(process, *[ctypes.byref(t) for t in times]), 'process_creation_query_failed')
        pid = self.kernel.GetProcessId(process)
        require(pid, 'process_pid_query_failed')
        return int(pid), (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def members(self, job):
        capacity = 1
        for _ in range(7):
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            returned = W.DWORD()
            if self.kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), ctypes.byref(returned)):
                return parse_members(buffer.raw, ctypes.sizeof(ctypes.c_size_t))
            require(ctypes.get_last_error() == 234 and capacity < 64, 'job_member_query_failed')
            capacity *= 2
        raise OwnershipError('job_member_query_unstable')

    def resume(self, thread):
        require(self.kernel.ResumeThread(thread) == 1, 'thread_resume_failed')

    def capture_member(self, job, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        require(handle, 'member_handle_unavailable')
        try:
            require(self.in_job(job, handle), 'member_no_longer_in_job')
            identity = self.identity(handle)
            require(identity[0] == pid, 'member_pid_differs')
            image = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(image))
            require(self.kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)), 'member_image_unavailable')
            return handle, {'pid': pid, 'creation_identity': identity, 'image': image.value}
        except BaseException:
            self.close(handle)
            raise

    def poll(self, process):
        result = self.winapi.WaitForSingleObject(process, 0)
        require(result in (0, 258), 'process_wait_failed')
        return self.winapi.GetExitCodeProcess(process) if result == 0 else None

    def terminate_job(self, job):
        if not self.kernel.TerminateJobObject(job, 1):
            error = OwnershipError('job_terminate_failed')
            error.winerror = ctypes.get_last_error()
            raise error

    def terminate_process(self, process):
        self.winapi.TerminateProcess(process, 1)

    def close(self, handle):
        require(self.kernel.CloseHandle(handle), 'owned_handle_close_failed')


class OwnedProcess:
    """Popen-shaped principal whose poll also checks the entire owned Job."""
    def __init__(self, argv, *, env, cwd, _api=None):
        self.api = _api if _api is not None else WindowsApi()
        self._handle = self.thread = self.job = self.stdout = None
        self.pid = None
        self.creation_identity = None
        self.closed = False
        self.assigned = False
        self.cleanup_receipt = None
        self.observed_members = []
        self.extra_handles = []
        self.member_records = []
        try:
            self.job = self.api.create_job()
            self._handle, self.thread, self.pid, self.stdout = self.api.create_suspended(argv, env, cwd)
            self.creation_identity = self.api.identity(self._handle)
            require(self.creation_identity[0] == self.pid, 'created_pid_differs')
            self.api.assign(self.job, self._handle)
            self.assigned = True
            self.verify_tree(require_alive=True)
            self.api.resume(self.thread)
            self.api.close(self.thread)
            self.thread = None
            self.verify_tree(require_alive=True)
        except BaseException as error:
            self.close_tree(grace=2)
            self.cleanup_receipt['errors'].extend(getattr(error,'suspended_cleanup_errors', []))
            if self.stdout is not None:
                try:
                    self.stdout.close()
                except BaseException:
                    self.cleanup_receipt['errors'].append('failed_launch_stream_close_failed')
            # Preserve the primary failure and cleanup evidence for the caller.
            try:
                error.cleanup_receipt = self.cleanup_receipt
            except Exception:
                pass
            raise

    def verify_tree(self, *, require_alive=False):
        require(not self.closed and self.job is not None and self._handle is not None, 'owned_tree_closed')
        require(self.api.identity(self._handle) == self.creation_identity, 'owned_principal_changed')
        require(self.api.in_job(self.job, self._handle), 'owned_principal_outside_job')
        status = self.api.poll(self._handle)
        members = self.api.members(self.job)
        self.observed_members = sorted(set(self.observed_members).union(members))
        if set(members) - {self.pid}:
            for pid in members:
                if pid == self.pid:
                    continue
                try:
                    handle, record = self.api.capture_member(self.job, pid)
                    self.extra_handles.append(handle)
                    self.member_records.append(record)
                except Exception:
                    self.member_records.append({'pid':pid, 'identity_status':'unavailable_or_exited'})
            raise OwnershipError('unexpected_owned_member')
        # The root may exit between polling and membership enumeration.
        if not members and status is None:
            status = self.api.poll(self._handle)
        require(set(members) <= {self.pid}, 'unexpected_owned_member')
        require((status is None and members == (self.pid,)) or (status is not None and not members), 'owned_membership_transition')
        require(not require_alive or status is None, 'owned_process_exited')
        return status

    def poll(self):
        return self.verify_tree()

    def close_tree(self, *, grace=5, clock=time.monotonic, sleeper=time.sleep):
        if self.closed:
            return list(self.cleanup_receipt['errors'])
        errors = []
        events = []
        members = None
        root_status = 'not_queried'
        job_termination_succeeded = False
        root_fallback_attempted = False
        settled = False
        def failure(operation, error):
            code = getattr(error, 'winerror', None)
            events.append({'operation':operation,'status':'error',
                'winerror':code if type(code) is int and 0 <= code <= 0xffffffff else None})
        try:
            if self.job is not None:
                try:
                    members = self.api.members(self.job)
                    self.observed_members = sorted(set(self.observed_members).union(members))
                except Exception as error:
                    errors.append('cleanup_membership_unavailable')
                    failure('members_before_termination', error)
                try:
                    self.api.terminate_job(self.job)
                    job_termination_succeeded = True
                    events.append({'operation':'terminate_job','status':'requested'})
                except Exception as error:
                    errors.append('owned_job_termination_failed')
                    failure('terminate_job', error)
            # A successful Job termination already requests root termination.
            # Do not race it with another TerminateProcess. Root fallback is
            # restricted to failed assignment/failed or unavailable Job request.
            if self._handle is not None and (not self.assigned or not job_termination_succeeded):
                try:
                    root_status = self.api.poll(self._handle)
                    if root_status is None:
                        root_fallback_attempted = True
                        try:
                            self.api.terminate_process(self._handle)
                            events.append({'operation':'terminate_root','status':'requested'})
                        except Exception as error:
                            failure('terminate_root', error)
                            # ERROR_ACCESS_DENIED can mean the retained process
                            # exited between poll and termination. Prove exit on
                            # that same handle; never treat error5 alone as safe.
                            root_status = self.api.poll(self._handle)
                            if type(getattr(error,'winerror',None)) is int and error.winerror == 5 and root_status is not None:
                                events.append({'operation':'terminate_root','status':'already_exited_after_error',
                                    'winerror':5,'exit_status':root_status})
                            else:
                                errors.append('owned_root_termination_failed')
                except Exception as error:
                    errors.append('owned_root_termination_failed')
                    failure('root_fallback_accounting', error)
            deadline = clock() + grace
            while True:
                try:
                    members = self.api.members(self.job) if self.job is not None else None
                    root_status = self.api.poll(self._handle) if self._handle is not None else 'no_root_created'
                    if not members and root_status is not None:
                        settled = True
                        break
                except Exception as error:
                    errors.append('cleanup_final_accounting_failed')
                    failure('final_accounting', error)
                    break
                if clock() >= deadline:
                    errors.append('owned_tree_cleanup_timeout')
                    break
                sleeper(.05)
        finally:
            job_close_fallback_requested = self.job is not None and not settled
            for handle in self.extra_handles:
                try:
                    self.api.close(handle)
                except Exception:
                    errors.append('owned_member_handle_close_failed')
            self.extra_handles = []
            for attribute in ('thread', 'job', '_handle'):
                handle = getattr(self, attribute)
                if handle is not None:
                    try:
                        self.api.close(handle)
                    except Exception:
                        errors.append('owned_' + attribute + '_close_failed')
                    setattr(self, attribute, None)
            # The bounded log reader owns stdout until it has drained; caller closes it.
            self.closed = True
            self.cleanup_receipt = {'pid': self.pid, 'creation_identity': self.creation_identity,
                'observed_member_pids': self.observed_members, 'final_member_pids': list(members) if members is not None else None,
                'unexpected_members': self.member_records,
                'job_assignment_completed':self.assigned,'job_termination_requested':job_termination_succeeded,
                'root_fallback_attempted':root_fallback_attempted,'root_exit_status':root_status,
                'cleanup_events':events,'errors':errors,
                'job_close_kill_policy':True,'job_close_fallback_requested':job_close_fallback_requested}
        return errors
