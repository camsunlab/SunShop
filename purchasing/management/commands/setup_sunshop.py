from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from purchasing.workflow import PI_GROUP, PURCHASER_GROUP


class Command(BaseCommand):
    help = "Create the PI and Purchaser groups, and optionally add users to them."

    def add_arguments(self, parser):
        parser.add_argument("--pi", nargs="*", default=[], metavar="USERNAME")
        parser.add_argument("--purchaser", nargs="*", default=[], metavar="USERNAME")

    def handle(self, *args, **options):
        for group_name, usernames in ((PI_GROUP, options["pi"]), (PURCHASER_GROUP, options["purchaser"])):
            group, created = Group.objects.get_or_create(name=group_name)
            self.stdout.write(f"{'Created' if created else 'Found'} group '{group_name}'")
            for username in usernames:
                try:
                    user = User.objects.get(username=username)
                except User.DoesNotExist:
                    raise CommandError(f"No user called '{username}'")
                user.groups.add(group)
                self.stdout.write(f"  added {username}")
